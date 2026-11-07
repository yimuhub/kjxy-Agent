"""工作区插件：路径闸门、差异预览、应用与回滚。"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.audit.models import AuditLog
from app.modules.library import manifest_v2
from app.modules.library.models import PluginConsent
from app.modules.plugins.models import PluginManifest
from app.modules.workspace import service

YARD = "yard-workspace"


def _ctx(yard: str = YARD) -> YardContext:
    return YardContext(
        yard_id=yard,
        user_id="u-ws",
        roles=["yard_admin"],
        request_id="req-ws",
    )


def _plugin(
    db,
    *,
    plugin_id: str = "workspace_editor",
    permissions: list[str] | None = None,
    yard_id: str | None = None,
    status: str = "enabled",
) -> PluginManifest:
    row = PluginManifest(
        plugin_id=plugin_id,
        yard_id=yard_id,
        version="1.0.0",
        type="workspace",
        status=status,
        manifest_json={
            "plugin_id": plugin_id,
            "name": "工作区编辑器",
            "version": "1.0.0",
            "type": "workspace",
            "permissions": permissions or ["filesystem_read", "filesystem_write"],
            "platforms": ["macos", "windows", "linux"],
            "schema_version": 2,
        },
        checksum="x",
        checksum_ok=True,
        installed_at=datetime.now(UTC),
    )
    db.add(row)
    db.flush()
    return row


def _grant(db, row: PluginManifest, *, yard_id: str = YARD) -> None:
    permissions = list(row.manifest_json["permissions"])
    db.add(
        PluginConsent(
            yard_id=yard_id,
            item_key=f"official:plugin:{row.plugin_id}",
            plugin_id=row.plugin_id,
            plugin_version=row.version,
            scope_hash=manifest_v2.scope_hash(permissions),
            permissions_json=permissions,
            risk_level=manifest_v2.risk_level(permissions),
            ack_version="v1",
            accepted_by="u-ws",
            accepted_at=datetime.now(UTC),
        )
    )
    db.flush()


@pytest.fixture()
def workspace_root(monkeypatch, tmp_path):
    monkeypatch.setenv("KJXY_WORKSPACE_ROOTS", str(tmp_path))
    return tmp_path


def _project(db, root: Path, *, name: str = "demo"):
    return service.create_project(
        db,
        _ctx(),
        name=name,
        root_path=str(root),
        description=None,
        allowed_paths=None,
    )


def test_create_project_and_reject_escape(db_session, workspace_root):
    project = _project(db_session, workspace_root)
    assert project["root_path"] == str(workspace_root.resolve())
    assert project["git"] is False

    with pytest.raises(AppError) as exc:
        service.read_file(db_session, _ctx(), project["id"], "../outside.txt")
    assert exc.value.code == "WORKSPACE_PATH_INVALID"

    with pytest.raises(AppError) as exc:
        service.read_file(db_session, _ctx(), project["id"], ".env")
    assert exc.value.code == "WORKSPACE_PATH_DENIED"


def test_project_root_must_be_under_allowed_roots(monkeypatch, db_session, tmp_path):
    other = tmp_path / "outside-root"
    other.mkdir()
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("KJXY_WORKSPACE_ROOTS", str(allowed))
    with pytest.raises(AppError) as exc:
        service.create_project(
            db_session,
            _ctx(),
            name="bad",
            root_path=str(other),
            description=None,
            allowed_paths=None,
        )
    assert exc.value.code == "WORKSPACE_ROOT_DENIED"


def test_path_allowed_globs_enforced(db_session, workspace_root):
    project = service.create_project(
        db_session,
        _ctx(),
        name="restricted",
        root_path=str(workspace_root),
        description=None,
        allowed_paths=["src/**"],
    )
    _plugin(db_session)
    with pytest.raises(AppError) as exc:
        service.preview_change(
            db_session,
            _ctx(),
            project_id=project["id"],
            plugin_id="workspace_editor",
            path="README.md",
            content="x",
        )
    assert exc.value.code == "WORKSPACE_PATH_DENIED"


def test_allowed_glob_star_does_not_cross_directories(db_session, workspace_root):
    project = service.create_project(
        db_session,
        _ctx(),
        name="narrow",
        root_path=str(workspace_root),
        description=None,
        allowed_paths=["src/*.py"],
    )
    _grant(db_session, _plugin(db_session))
    allowed = service.preview_change(
        db_session,
        _ctx(),
        project_id=project["id"],
        plugin_id="workspace_editor",
        path="src/app.py",
        content="x = 1\n",
    )
    assert allowed["path"] == "src/app.py"
    with pytest.raises(AppError) as exc:
        service.preview_change(
            db_session,
            _ctx(),
            project_id=project["id"],
            plugin_id="workspace_editor",
            path="src/nested/app.py",
            content="x = 1\n",
        )
    assert exc.value.code == "WORKSPACE_PATH_DENIED"


def test_allowed_glob_double_star_crosses_directories(db_session, workspace_root):
    project = service.create_project(
        db_session,
        _ctx(),
        name="wide",
        root_path=str(workspace_root),
        description=None,
        allowed_paths=["src/**/*.py"],
    )
    _grant(db_session, _plugin(db_session))
    change = service.preview_change(
        db_session,
        _ctx(),
        project_id=project["id"],
        plugin_id="workspace_editor",
        path="src/nested/app.py",
        content="x = 1\n",
    )
    assert change["path"] == "src/nested/app.py"


def test_symlink_escape_is_rejected(db_session, workspace_root, tmp_path):
    outside = tmp_path.parent / "ws-outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = workspace_root / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("当前环境不支持创建符号链接")
    project = _project(db_session, workspace_root)
    with pytest.raises(AppError) as exc:
        service.read_file(db_session, _ctx(), project["id"], "link.txt")
    assert exc.value.code == "WORKSPACE_PATH_INVALID"


def test_preview_apply_and_audit(db_session, workspace_root):
    row = _plugin(db_session)
    _grant(db_session, row)
    target = workspace_root / "src" / "app.py"
    target.parent.mkdir()
    target.write_text("print('old')\n", encoding="utf-8")
    project = _project(db_session, workspace_root)

    change = service.preview_change(
        db_session,
        _ctx(),
        project_id=project["id"],
        plugin_id=row.plugin_id,
        path="src/app.py",
        content="print('new')\nprint('more')\n",
    )
    assert change["status"] == "preview"
    assert change["added_lines"] == 2
    assert change["removed_lines"] == 1
    assert "+print('new')" in change["diff"]
    assert target.read_text(encoding="utf-8") == "print('old')\n"

    applied = service.apply_change(db_session, _ctx(), change["id"])
    assert applied["status"] == "applied"
    assert target.read_text(encoding="utf-8") == "print('new')\nprint('more')\n"
    actions = {log.action for log in db_session.query(AuditLog).all()}
    assert {"workspace_change_preview", "workspace_change_applied"} <= actions


def test_preview_requires_declared_permission_and_consent(db_session, workspace_root):
    read_only = _plugin(db_session, plugin_id="reader", permissions=["filesystem_read"])
    _grant(db_session, read_only)
    project = _project(db_session, workspace_root)
    with pytest.raises(AppError) as exc:
        service.preview_change(
            db_session,
            _ctx(),
            project_id=project["id"],
            plugin_id="reader",
            path="a.txt",
            content="x",
        )
    assert exc.value.code == "RUNTIME_PERMISSION_NOT_DECLARED"

    writer = _plugin(db_session, plugin_id="writer")
    with pytest.raises(AppError) as exc:
        service.preview_change(
            db_session,
            _ctx(),
            project_id=project["id"],
            plugin_id="writer",
            path="a.txt",
            content="x",
        )
    assert exc.value.code == "RUNTIME_CONSENT_REQUIRED"
    assert writer.plugin_id == "writer"


def test_apply_rejects_file_changed_after_preview(db_session, workspace_root):
    row = _plugin(db_session)
    _grant(db_session, row)
    target = workspace_root / "a.txt"
    target.write_text("one", encoding="utf-8")
    project = _project(db_session, workspace_root)
    change = service.preview_change(
        db_session,
        _ctx(),
        project_id=project["id"],
        plugin_id=row.plugin_id,
        path="a.txt",
        content="two",
    )
    target.write_text("someone else", encoding="utf-8")
    with pytest.raises(AppError) as exc:
        service.apply_change(db_session, _ctx(), change["id"])
    assert exc.value.code == "WORKSPACE_FILE_CHANGED"


def test_rollback_restores_before_or_deletes_new_file(db_session, workspace_root):
    row = _plugin(db_session)
    _grant(db_session, row)
    target = workspace_root / "a.txt"
    target.write_text("original", encoding="utf-8")
    project = _project(db_session, workspace_root)

    change = service.preview_change(
        db_session,
        _ctx(),
        project_id=project["id"],
        plugin_id=row.plugin_id,
        path="a.txt",
        content="changed",
    )
    service.apply_change(db_session, _ctx(), change["id"])
    rolled = service.rollback_change(db_session, _ctx(), change["id"])
    assert rolled["status"] == "rolled_back"
    assert target.read_text(encoding="utf-8") == "original"

    created = service.preview_change(
        db_session,
        _ctx(),
        project_id=project["id"],
        plugin_id=row.plugin_id,
        path="new.txt",
        content="brand new",
    )
    service.apply_change(db_session, _ctx(), created["id"])
    assert (workspace_root / "new.txt").exists()
    service.rollback_change(db_session, _ctx(), created["id"])
    assert not (workspace_root / "new.txt").exists()


def test_rollback_refuses_when_file_changed_unless_forced(db_session, workspace_root):
    row = _plugin(db_session)
    _grant(db_session, row)
    target = workspace_root / "a.txt"
    target.write_text("original", encoding="utf-8")
    project = _project(db_session, workspace_root)
    change = service.preview_change(
        db_session,
        _ctx(),
        project_id=project["id"],
        plugin_id=row.plugin_id,
        path="a.txt",
        content="changed",
    )
    service.apply_change(db_session, _ctx(), change["id"])
    target.write_text("manual edit", encoding="utf-8")
    with pytest.raises(AppError) as exc:
        service.rollback_change(db_session, _ctx(), change["id"])
    assert exc.value.code == "WORKSPACE_FILE_CHANGED"
    service.rollback_change(db_session, _ctx(), change["id"], force=True)
    assert target.read_text(encoding="utf-8") == "original"


def test_git_rollback_restores_preview_snapshot_not_head(db_session, workspace_root):
    """git 仓库里回滚以预览快照为准，不能抹掉预览前的未提交改动。"""
    if not (Path("/usr/bin/git").exists() or Path("/opt/homebrew/bin/git").exists()):
        pytest.skip("当前环境没有 git")
    row = _plugin(db_session)
    _grant(db_session, row)
    subprocess.run(["git", "init", "-q"], cwd=workspace_root, check=True)
    target = workspace_root / "app.py"
    target.write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=workspace_root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "init",
        ],
        cwd=workspace_root,
        check=True,
    )
    target.write_text("v1-uncommitted\n", encoding="utf-8")
    project = _project(db_session, workspace_root)
    change = service.preview_change(
        db_session,
        _ctx(),
        project_id=project["id"],
        plugin_id=row.plugin_id,
        path="app.py",
        content="v2\n",
    )
    assert change["git_head_before"]
    service.apply_change(db_session, _ctx(), change["id"])
    rolled = service.rollback_change(db_session, _ctx(), change["id"])
    assert rolled["status"] == "rolled_back"
    assert target.read_text(encoding="utf-8") == "v1-uncommitted\n"


def test_binary_and_oversize_rejected(db_session, workspace_root):
    row = _plugin(db_session)
    _grant(db_session, row)
    project = _project(db_session, workspace_root)
    with pytest.raises(AppError) as exc:
        service.preview_change(
            db_session,
            _ctx(),
            project_id=project["id"],
            plugin_id=row.plugin_id,
            path="blob.bin",
            content="abc\x00def",
        )
    assert exc.value.code == "WORKSPACE_BINARY_UNSUPPORTED"
    with pytest.raises(AppError) as exc:
        service.preview_change(
            db_session,
            _ctx(),
            project_id=project["id"],
            plugin_id=row.plugin_id,
            path="big.txt",
            content="x" * (service.MAX_FILE_BYTES + 1),
        )
    assert exc.value.code == "WORKSPACE_FILE_TOO_LARGE"


def test_cross_yard_project_is_404(db_session, workspace_root):
    project = _project(db_session, workspace_root)
    with pytest.raises(AppError) as exc:
        service.read_file(db_session, _ctx("other-yard"), project["id"], "a.txt")
    assert exc.value.http_status == 404


def test_status_lists_authorized_plugins(db_session, workspace_root):
    row = _plugin(db_session)
    _grant(db_session, row)
    _plugin(db_session, plugin_id="read-only", permissions=["filesystem_read"])
    result = service.status(db_session, _ctx())
    ids = {item["plugin_id"]: item for item in result["plugins"]}
    assert ids[row.plugin_id]["authorized"] is True
    assert "read-only" not in ids
    assert result["git_available"] is True or result["git_available"] is False
    assert result["allowed_roots"] == [str(workspace_root.resolve())]


def _symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("当前环境不支持创建符号链接")


def test_symlink_to_sensitive_file_inside_root_is_rejected(db_session, workspace_root):
    """输入名正常、resolve 后指向根内 .env：必须按真实目标拒绝。"""
    secret = workspace_root / ".env"
    secret.write_text("TOKEN=x", encoding="utf-8")
    _symlink(workspace_root / "notes.txt", secret)
    project = _project(db_session, workspace_root)
    with pytest.raises(AppError) as exc:
        service.read_file(db_session, _ctx(), project["id"], "notes.txt")
    assert exc.value.code == "WORKSPACE_PATH_DENIED"


def test_symlinked_directory_into_denied_dir_is_rejected(db_session, workspace_root):
    aws = workspace_root / ".aws"
    aws.mkdir()
    (aws / "credentials").write_text("secret", encoding="utf-8")
    _symlink(workspace_root / "config", aws)
    project = _project(db_session, workspace_root)
    with pytest.raises(AppError) as exc:
        service.read_file(db_session, _ctx(), project["id"], "config/credentials")
    assert exc.value.code == "WORKSPACE_PATH_DENIED"


def test_symlink_cannot_escape_allowed_paths(db_session, workspace_root):
    readme = workspace_root / "README.md"
    readme.write_text("readme", encoding="utf-8")
    src = workspace_root / "src"
    src.mkdir()
    _symlink(src / "evil.txt", readme)
    project = service.create_project(
        db_session,
        _ctx(),
        name="narrow-link",
        root_path=str(workspace_root),
        description=None,
        allowed_paths=["src/**"],
    )
    with pytest.raises(AppError) as exc:
        service.read_file(db_session, _ctx(), project["id"], "src/evil.txt")
    assert exc.value.code == "WORKSPACE_PATH_DENIED"
    with pytest.raises(AppError) as exc:
        service.preview_change(
            db_session,
            _ctx(),
            project_id=project["id"],
            plugin_id="workspace_editor",
            path="src/evil.txt",
            content="overwrite",
        )
    assert exc.value.code == "WORKSPACE_PATH_DENIED"
    assert readme.read_text(encoding="utf-8") == "readme"


def test_sensitive_directory_cannot_be_root(db_session, workspace_root):
    ssh = workspace_root / ".ssh"
    ssh.mkdir()
    with pytest.raises(AppError) as exc:
        service.create_project(
            db_session,
            _ctx(),
            name="ssh-root",
            root_path=str(ssh),
            description=None,
            allowed_paths=None,
        )
    assert exc.value.code == "WORKSPACE_ROOT_DENIED"
    alias = workspace_root / "alias"
    _symlink(alias, ssh)
    with pytest.raises(AppError) as exc:
        service.create_project(
            db_session,
            _ctx(),
            name="alias-root",
            root_path=str(alias),
            description=None,
            allowed_paths=None,
        )
    assert exc.value.code == "WORKSPACE_ROOT_DENIED"


def test_link_switch_to_sensitive_between_preview_and_apply(db_session, workspace_root):
    row = _plugin(db_session)
    _grant(db_session, row)
    normal = workspace_root / "normal.txt"
    normal.write_text("one", encoding="utf-8")
    secret = workspace_root / ".env"
    secret.write_text("TOKEN=x", encoding="utf-8")
    link = workspace_root / "entry.txt"
    _symlink(link, normal)
    project = _project(db_session, workspace_root)
    change = service.preview_change(
        db_session,
        _ctx(),
        project_id=project["id"],
        plugin_id=row.plugin_id,
        path="entry.txt",
        content="two",
    )
    link.unlink()
    link.symlink_to(secret)
    with pytest.raises(AppError) as exc:
        service.apply_change(db_session, _ctx(), change["id"])
    assert exc.value.code == "WORKSPACE_PATH_DENIED"
    assert secret.read_text(encoding="utf-8") == "TOKEN=x"
    assert normal.read_text(encoding="utf-8") == "one"


def test_link_switch_to_outside_root_between_preview_and_apply(
    db_session, workspace_root, tmp_path
):
    row = _plugin(db_session)
    _grant(db_session, row)
    normal = workspace_root / "normal.txt"
    normal.write_text("one", encoding="utf-8")
    outside = tmp_path.parent / "ws-switch-outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = workspace_root / "entry.txt"
    _symlink(link, normal)
    project = _project(db_session, workspace_root)
    change = service.preview_change(
        db_session,
        _ctx(),
        project_id=project["id"],
        plugin_id=row.plugin_id,
        path="entry.txt",
        content="two",
    )
    link.unlink()
    link.symlink_to(outside)
    with pytest.raises(AppError) as exc:
        service.apply_change(db_session, _ctx(), change["id"])
    assert exc.value.code == "WORKSPACE_PATH_INVALID"
    assert outside.read_text(encoding="utf-8") == "outside"
