"""工作区插件服务（v3.63）：受控项目、差异预览、应用与回滚。

安全口径：
- 项目根必须在允许根内（默认用户主目录，可用 ``KJXY_WORKSPACE_ROOTS`` 覆盖）；
- 相对路径解析后必须仍在项目根内，拒绝 ``..``、绝对路径与敏感文件；
- 只处理 UTF-8 文本，单文件 1MB 上限；应用前复核文件未被他人改动；
- 修改必须由已启用、已声明 ``filesystem_write`` 权限且有有效授权的插件发起；
- 回滚不依赖插件授权（安全动作），但仍要求小院写权限并留审计。
"""

from __future__ import annotations

import difflib
import fnmatch
import hashlib
import os
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.audit.service import log_audit
from app.modules.library import manifest_v2
from app.modules.library.models import PluginConsent
from app.modules.local_runtime import service as runtime_service
from app.modules.plugins.models import PluginManifest
from app.modules.workspace.models import (
    CHANGE_STATUS_APPLIED,
    CHANGE_STATUS_PREVIEW,
    CHANGE_STATUS_ROLLED_BACK,
    PROJECT_STATUS_ACTIVE,
    WorkspaceChange,
    WorkspaceProject,
)

MAX_FILE_BYTES = 1_048_576
MAX_DIFF_CHARS = 200_000
PREVIEW_TTL_MINUTES = 30
MAX_CHANGES_PER_LIST = 100
PLUGIN_TYPES = ("workspace", "local_runtime")

_DENIED_PARTS = {".git", ".ssh", ".aws", ".gnupg", ".kube", ".docker"}
_DENIED_NAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "credentials",
    "secret.key",
    "supplier.key",
}
_DENIED_SUFFIXES = (
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".keystore",
    ".jks",
    ".sqlite",
    ".db",
)
_HARD_DENIED_ROOTS = {
    Path("/etc"),
    Path("/usr"),
    Path("/bin"),
    Path("/sbin"),
    Path("/var"),
    Path("/boot"),
    Path("/dev"),
    Path("/proc"),
    Path("/sys"),
    Path("/System"),
    Path("/Library"),
}


def _fail(code: str, message: str, status: int = 400, **details: Any) -> AppError:
    return AppError(code, message, http_status=status, details=details or None)


def allowed_roots() -> list[Path]:
    raw = os.environ.get("KJXY_WORKSPACE_ROOTS", "")
    roots = [
        Path(item.strip()).expanduser().resolve() for item in raw.split(os.pathsep) if item.strip()
    ]
    return roots or [Path.home().resolve()]


def _root(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise _fail("WORKSPACE_ROOT_INVALID", "项目根必须是绝对路径。")
    try:
        root = path.resolve(strict=True)
    except OSError as e:
        raise _fail(
            "WORKSPACE_ROOT_INVALID", "项目根不存在或不可访问。", detail=str(e)[:120]
        ) from e
    if not root.is_dir():
        raise _fail("WORKSPACE_ROOT_INVALID", "项目根必须是目录。")
    if root == Path("/") or root in _HARD_DENIED_ROOTS:
        raise _fail("WORKSPACE_ROOT_DENIED", "不允许把系统目录注册为工作区。")
    if not any(root.is_relative_to(base) for base in allowed_roots()):
        raise _fail(
            "WORKSPACE_ROOT_DENIED",
            "项目根不在允许范围内。",
            allowed_roots=[str(item) for item in allowed_roots()],
        )
    return root


def _normalize_rel(raw: str) -> str:
    text = raw.strip().replace("\\", "/")
    if not text or "\x00" in text:
        raise _fail("WORKSPACE_PATH_INVALID", "文件路径不能为空。")
    pure = PurePosixPath(text)
    if pure.is_absolute() or ".." in pure.parts:
        raise _fail("WORKSPACE_PATH_INVALID", "文件路径必须是项目内的相对路径。")
    rel = pure.as_posix()
    if not rel or rel == ".":
        raise _fail("WORKSPACE_PATH_INVALID", "文件路径不能是项目根。")
    lower_parts = {part.casefold() for part in PurePosixPath(rel).parts}
    if lower_parts & _DENIED_PARTS:
        raise _fail("WORKSPACE_PATH_DENIED", "该目录不允许修改。")
    name = PurePosixPath(rel).name.casefold()
    if name in _DENIED_NAMES or name.startswith(".env.") or name.endswith(_DENIED_SUFFIXES):
        raise _fail("WORKSPACE_PATH_DENIED", "敏感文件不允许修改。")
    return rel


def _matches_allowed(rel: str, patterns: list[str] | None) -> bool:
    if not patterns:
        return True
    for pattern in patterns:
        if pattern in ("*", "**", "**/*", "./**/*"):
            return True
        if pattern.endswith("/**") and rel.startswith(pattern[:-3].rstrip("/") + "/"):
            return True
        if PurePosixPath(rel).match(pattern) or fnmatch.fnmatchcase(rel, pattern):
            return True
    return False


def _target(project: WorkspaceProject, raw: str) -> tuple[str, Path]:
    rel = _normalize_rel(raw)
    if not _matches_allowed(rel, project.allowed_paths):
        raise _fail(
            "WORKSPACE_PATH_DENIED",
            "该路径不在项目允许修改的范围内。",
            allowed_paths=project.allowed_paths or [],
        )
    root = Path(project.root_path).resolve()
    target = (root / rel).resolve()
    if not target.is_relative_to(root):
        raise _fail("WORKSPACE_PATH_INVALID", "文件路径解析后越出项目根。", path=rel)
    return rel, target


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_text(target: Path) -> tuple[bool, str]:
    if not target.exists():
        return False, ""
    if not target.is_file():
        raise _fail("WORKSPACE_PATH_INVALID", "目标不是普通文件。")
    if target.stat().st_size > MAX_FILE_BYTES:
        raise _fail(
            "WORKSPACE_FILE_TOO_LARGE",
            f"文件超过 {MAX_FILE_BYTES // 1024}KB，不能在工作区修改。",
            status=413,
        )
    data = target.read_bytes()
    if b"\x00" in data[:4096]:
        raise _fail("WORKSPACE_BINARY_UNSUPPORTED", "不支持修改二进制文件。", status=415)
    try:
        return True, data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise _fail("WORKSPACE_BINARY_UNSUPPORTED", "文件不是 UTF-8 文本。", status=415) from e


def _sha_file(target: Path) -> str | None:
    if not target.exists():
        return None
    if target.stat().st_size > MAX_FILE_BYTES:
        raise _fail("WORKSPACE_FILE_TOO_LARGE", "文件已超过 1MB。", status=413)
    return _sha_bytes(target.read_bytes())


def _diff(rel: str, before: str, after: str) -> tuple[str, int, int, bool]:
    chunks = list(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
            n=3,
        )
    )
    text = "".join(chunks)
    added = sum(1 for line in chunks if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in chunks if line.startswith("-") and not line.startswith("---"))
    truncated = len(text) > MAX_DIFF_CHARS
    if truncated:
        text = text[:MAX_DIFF_CHARS] + "\n…（差异过长，已截断）\n"
    return text, added, removed, truncated


def _git(root: Path, *args: str) -> str | None:
    if shutil.which("git") is None:
        return None
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            timeout=5,
            check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.decode("utf-8", errors="replace").strip()


def _git_head(root: Path) -> str | None:
    if _git(root, "rev-parse", "--is-inside-work-tree") != "true":
        return None
    return _git(root, "rev-parse", "HEAD")


def _is_git(root: Path) -> bool:
    return _git(root, "rev-parse", "--is-inside-work-tree") == "true"


def _project(db: Session, ctx: YardContext, project_id: str) -> WorkspaceProject:
    row = (
        db.query(WorkspaceProject)
        .filter(
            WorkspaceProject.id == project_id,
            WorkspaceProject.yard_id == ctx.yard_id,
            WorkspaceProject.status == PROJECT_STATUS_ACTIVE,
        )
        .first()
    )
    if row is None:
        raise AppError(
            "WORKSPACE_PROJECT_NOT_FOUND",
            "工作区不存在或已归档。",
            http_status=404,
            details={"project_id": project_id},
        )
    return row


def _change(db: Session, ctx: YardContext, change_id: str) -> WorkspaceChange:
    row = (
        db.query(WorkspaceChange)
        .filter(WorkspaceChange.id == change_id, WorkspaceChange.yard_id == ctx.yard_id)
        .first()
    )
    if row is None:
        raise AppError(
            "WORKSPACE_CHANGE_NOT_FOUND",
            "变更记录不存在。",
            http_status=404,
            details={"change_id": change_id},
        )
    return row


def _audit(
    db: Session,
    ctx: YardContext,
    action: str,
    resource_id: str,
    *,
    result: str = "success",
    metadata: dict[str, Any] | None = None,
) -> None:
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action=action,
        resource_type="workspace",
        resource_id=resource_id,
        result=result,
        request_id=ctx.request_id,
        metadata=metadata,
    )


def serialize_project(row: WorkspaceProject, *, with_git: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": row.id,
        "name": row.name,
        "root_path": row.root_path,
        "description": row.description or "",
        "allowed_paths": row.allowed_paths or ["**/*"],
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
    if with_git:
        payload["git"] = _is_git(Path(row.root_path))
        payload["git_head"] = _git_head(Path(row.root_path))
    return payload


def serialize_change(row: WorkspaceChange) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "plugin_id": row.plugin_id,
        "path": row.path,
        "status": row.status,
        "before_existed": row.before_existed,
        "before_sha": row.before_sha,
        "after_sha": row.after_sha,
        "diff": row.diff_text,
        "diff_truncated": row.diff_truncated,
        "added_lines": row.added_lines,
        "removed_lines": row.removed_lines,
        "git_head_before": row.git_head_before,
        "git_head_after": row.git_head_after,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "applied_at": row.applied_at.isoformat() if row.applied_at else None,
        "rolled_back_at": row.rolled_back_at.isoformat() if row.rolled_back_at else None,
        "error": row.error_message or "",
    }


def list_projects(db: Session, ctx: YardContext) -> list[dict[str, Any]]:
    rows = (
        db.query(WorkspaceProject)
        .filter(
            WorkspaceProject.yard_id == ctx.yard_id,
            WorkspaceProject.status == PROJECT_STATUS_ACTIVE,
        )
        .order_by(WorkspaceProject.created_at.desc())
        .all()
    )
    return [serialize_project(row) for row in rows]


def create_project(
    db: Session,
    ctx: YardContext,
    *,
    name: str,
    root_path: str,
    description: str | None,
    allowed_paths: list[str] | None,
) -> dict[str, Any]:
    clean_name = name.strip()
    if not clean_name:
        raise _fail("WORKSPACE_PROJECT_INVALID", "项目名称不能为空。")
    duplicate = (
        db.query(WorkspaceProject)
        .filter(
            WorkspaceProject.yard_id == ctx.yard_id,
            WorkspaceProject.name == clean_name,
        )
        .first()
    )
    if duplicate is not None:
        raise AppError(
            "WORKSPACE_PROJECT_EXISTS",
            "同名工作区已存在。",
            http_status=409,
            details={"name": clean_name},
        )
    root = _root(root_path)
    patterns = [str(item).strip() for item in (allowed_paths or []) if str(item).strip()]
    for pattern in patterns:
        if ".." in PurePosixPath(pattern).parts:
            raise _fail("WORKSPACE_PATH_INVALID", "允许路径不能包含 ..。")
    row = WorkspaceProject(
        yard_id=ctx.yard_id,
        name=clean_name,
        root_path=str(root),
        description=(description or "").strip() or None,
        allowed_paths=patterns or ["**/*"],
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
    )
    db.add(row)
    db.flush()
    _audit(db, ctx, "workspace_project_created", row.id, metadata={"root": str(root)})
    return serialize_project(row, with_git=True)


def archive_project(db: Session, ctx: YardContext, project_id: str) -> dict[str, Any]:
    row = _project(db, ctx, project_id)
    row.status = "archived"
    row.updated_by = ctx.user_id
    _audit(db, ctx, "workspace_project_archived", row.id)
    return serialize_project(row)


def project_detail(db: Session, ctx: YardContext, project_id: str) -> dict[str, Any]:
    return serialize_project(_project(db, ctx, project_id), with_git=True)


def read_file(db: Session, ctx: YardContext, project_id: str, raw_path: str) -> dict[str, Any]:
    project = _project(db, ctx, project_id)
    rel, target = _target(project, raw_path)
    existed, content = _read_text(target)
    return {
        "path": rel,
        "exists": existed,
        "content": content,
        "size_bytes": len(content.encode("utf-8")),
        "sha256": _sha_bytes(content.encode("utf-8")),
    }


def preview_change(
    db: Session,
    ctx: YardContext,
    *,
    project_id: str,
    plugin_id: str,
    path: str,
    content: str,
) -> dict[str, Any]:
    project = _project(db, ctx, project_id)
    rel, target = _target(project, path)
    runtime_service.authorize_plugin(db, ctx, plugin_id, ["filesystem_write"], types=PLUGIN_TYPES)
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        raise _fail(
            "WORKSPACE_FILE_TOO_LARGE",
            f"修改内容超过 {MAX_FILE_BYTES // 1024}KB。",
            status=413,
        )
    if "\x00" in content:
        raise _fail("WORKSPACE_BINARY_UNSUPPORTED", "不支持写入二进制内容。", status=415)
    existed, before = _read_text(target)
    if existed and before == content:
        raise _fail("WORKSPACE_NO_CHANGES", "内容与当前文件一致，没有可预览的修改。")
    diff_text, added, removed, truncated = _diff(rel, before if existed else "", content)
    change = WorkspaceChange(
        yard_id=ctx.yard_id,
        project_id=project.id,
        plugin_id=plugin_id,
        path=rel,
        status=CHANGE_STATUS_PREVIEW,
        before_existed=existed,
        before_content=before if existed else None,
        before_sha=_sha_bytes(before.encode("utf-8")) if existed else None,
        after_content=content,
        after_sha=_sha_bytes(content.encode("utf-8")),
        diff_text=diff_text,
        diff_truncated=truncated,
        added_lines=added,
        removed_lines=removed,
        git_head_before=_git_head(Path(project.root_path)),
        created_by=ctx.user_id,
    )
    db.add(change)
    db.flush()
    _audit(
        db,
        ctx,
        "workspace_change_preview",
        change.id,
        metadata={"project_id": project.id, "path": rel, "plugin_id": plugin_id},
    )
    return serialize_change(change)


def _assert_preview_fresh(change: WorkspaceChange) -> None:
    if change.status != CHANGE_STATUS_PREVIEW:
        raise AppError(
            "WORKSPACE_CHANGE_STATE",
            "该变更已不是待应用状态。",
            http_status=409,
            details={"status": change.status},
        )
    created = change.created_at
    if created is not None:
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        if datetime.now(UTC) - created > timedelta(minutes=PREVIEW_TTL_MINUTES):
            raise AppError(
                "WORKSPACE_PREVIEW_EXPIRED",
                "预览已超过 30 分钟，请重新预览。",
                http_status=409,
            )


def apply_change(db: Session, ctx: YardContext, change_id: str) -> dict[str, Any]:
    change = _change(db, ctx, change_id)
    _assert_preview_fresh(change)
    project = _project(db, ctx, change.project_id)
    rel, target = _target(project, change.path)
    runtime_service.authorize_plugin(
        db, ctx, change.plugin_id, ["filesystem_write"], types=PLUGIN_TYPES
    )
    current_sha = _sha_file(target)
    if current_sha != change.before_sha:
        raise AppError(
            "WORKSPACE_FILE_CHANGED",
            "文件在预览后已被修改，请重新预览。",
            http_status=409,
            details={"path": rel, "current_sha": current_sha, "preview_sha": change.before_sha},
        )
    after = change.after_content
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=".kjxy-", dir=str(target.parent))
    os.close(handle)
    temp = Path(temp_name)
    try:
        with temp.open("w", encoding="utf-8", newline="") as out:
            out.write(after)
        if target.exists():
            os.chmod(temp, target.stat().st_mode & 0o777)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    if _sha_bytes(target.read_bytes()) != change.after_sha:
        raise _fail("WORKSPACE_APPLY_FAILED", "写入后校验不一致。", status=500)
    change.status = CHANGE_STATUS_APPLIED
    change.applied_by = ctx.user_id
    change.applied_at = datetime.now(UTC)
    change.git_head_after = _git_head(Path(project.root_path))
    _audit(
        db,
        ctx,
        "workspace_change_applied",
        change.id,
        metadata={"project_id": project.id, "path": rel, "plugin_id": change.plugin_id},
    )
    return serialize_change(change)


def rollback_change(
    db: Session, ctx: YardContext, change_id: str, *, force: bool = False
) -> dict[str, Any]:
    change = _change(db, ctx, change_id)
    if change.status != CHANGE_STATUS_APPLIED:
        raise AppError(
            "WORKSPACE_CHANGE_STATE",
            "只有已应用的修改可以回滚。",
            http_status=409,
            details={"status": change.status},
        )
    project = _project(db, ctx, change.project_id)
    rel, target = _target(project, change.path)
    current_sha = _sha_file(target)
    if not force and current_sha != change.after_sha:
        raise AppError(
            "WORKSPACE_FILE_CHANGED",
            "文件在应用后又被修改；确认放弃这些修改请使用 force。",
            http_status=409,
            details={"path": rel, "current_sha": current_sha, "applied_sha": change.after_sha},
        )
    restored_by_git = False
    if change.git_head_before:
        restored = _git(
            Path(project.root_path),
            "restore",
            "--source",
            change.git_head_before,
            "--worktree",
            "--",
            rel,
        )
        if restored:
            restored_by_git = True
    if not restored_by_git:
        if change.before_existed and change.before_content is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w", encoding="utf-8", newline="") as out:
                out.write(change.before_content)
        else:
            target.unlink(missing_ok=True)
    change.status = CHANGE_STATUS_ROLLED_BACK
    change.rolled_back_by = ctx.user_id
    change.rolled_back_at = datetime.now(UTC)
    _audit(
        db,
        ctx,
        "workspace_change_rolled_back",
        change.id,
        metadata={"project_id": project.id, "path": rel, "via_git": restored_by_git},
    )
    return serialize_change(change)


def list_changes(db: Session, ctx: YardContext, project_id: str) -> list[dict[str, Any]]:
    _project(db, ctx, project_id)
    rows = (
        db.query(WorkspaceChange)
        .filter(
            WorkspaceChange.yard_id == ctx.yard_id,
            WorkspaceChange.project_id == project_id,
        )
        .order_by(WorkspaceChange.created_at.desc())
        .limit(MAX_CHANGES_PER_LIST)
        .all()
    )
    return [serialize_change(row) for row in rows]


def status(db: Session, ctx: YardContext) -> dict[str, Any]:
    rows = (
        db.query(PluginManifest)
        .filter(
            PluginManifest.type.in_(PLUGIN_TYPES),
            or_(PluginManifest.yard_id.is_(None), PluginManifest.yard_id == ctx.yard_id),
        )
        .order_by(PluginManifest.plugin_id)
        .all()
    )
    plugins: list[dict[str, Any]] = []
    for row in rows:
        permissions_raw = (row.manifest_json or {}).get("permissions") or []
        permissions = manifest_v2.normalize([str(item) for item in permissions_raw])
        if "filesystem_write" not in permissions:
            continue
        scope_hash = manifest_v2.scope_hash(permissions)
        consent = (
            db.query(PluginConsent)
            .filter(
                PluginConsent.yard_id == ctx.yard_id,
                PluginConsent.plugin_id == row.plugin_id,
                PluginConsent.scope_hash == scope_hash,
                PluginConsent.revoked_at.is_(None),
            )
            .first()
        )
        plugins.append(
            {
                "plugin_id": row.plugin_id,
                "name": str((row.manifest_json or {}).get("name") or row.plugin_id),
                "version": row.version,
                "enabled": row.status == "enabled",
                "authorized": consent is not None,
                "permissions": permissions,
            }
        )
    return {
        "allowed_roots": [str(item) for item in allowed_roots()],
        "max_file_bytes": MAX_FILE_BYTES,
        "preview_ttl_minutes": PREVIEW_TTL_MINUTES,
        "git_available": shutil.which("git") is not None,
        "plugins": plugins,
    }
