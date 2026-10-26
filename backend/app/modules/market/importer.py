"""本地导入：Skill 文本文件与插件 zip 包。

安全口径：
- Skill 只接受 .md/.markdown/.txt 且 ≤256KB；
- 插件 zip ≤2MB，只允许 manifest.json 与文本文件，拒绝路径穿越、
  绝对路径、符号链接与可执行文件；
- 导入内容只读解析，不执行任何文件；落地默认待审。
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.evolution.service import create_skill
from app.modules.market.models import MarketItem
from app.modules.market.skilldoc import (
    extract_list_items,
    extract_sections,
    filename_stem,
    first_heading,
    first_paragraph,
    parse_frontmatter,
    section_text,
    slugify,
    truncate,
)
from app.modules.plugins.models import PluginManifest
from app.modules.plugins.service import compute_checksum, validate_manifest

MAX_SKILL_BYTES = 256 * 1024
MAX_PLUGIN_BYTES = 2 * 1024 * 1024
MAX_PLUGIN_UNCOMPRESSED = 5 * 1024 * 1024
MAX_MANIFEST_BYTES = 512 * 1024
ALLOWED_SKILL_EXT = {".md", ".markdown", ".txt"}
ALLOWED_PLUGIN_EXT = {".json", ".md", ".txt", ".yaml", ".yml", ".csv"}
EXECUTABLE_EXT = {
    ".py",
    ".pyc",
    ".sh",
    ".bash",
    ".zsh",
    ".ps1",
    ".bat",
    ".cmd",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".bin",
    ".js",
    ".mjs",
    ".jar",
    ".pl",
    ".rb",
}


def _as_bytes(data: bytes | str) -> bytes:
    if isinstance(data, bytes):
        return data
    if isinstance(data, str):
        return data.encode("utf-8")
    raise AppError("INVALID_REQUEST", "文件内容格式不正确。", http_status=400)


def unique_skill_code(db: Session, yard_id: str, base: str) -> str:
    """小院 Skill 编码唯一（同时避开本地市场条目的 code）。"""
    from app.modules.evolution.models import Skill

    candidate = base
    index = 1
    while True:
        local_exists = (
            db.query(MarketItem.id)
            .filter(
                MarketItem.source == "local",
                MarketItem.item_type == "skill",
                MarketItem.code == candidate,
            )
            .first()
        )
        yard_exists = (
            db.query(Skill.id)
            .filter(Skill.yard_id == yard_id, Skill.skill_code == candidate)
            .first()
        )
        if local_exists is None and yard_exists is None:
            return candidate
        index += 1
        candidate = f"{base}-{index}"
        if index > 50:
            raise AppError("IMPORT_CONFLICT", "同名技能过多，无法生成唯一编码。", http_status=409)


def unique_plugin_id(db: Session, yard_id: str | None, base: str) -> str:
    candidate = base
    index = 1
    while True:
        exists = (
            db.query(PluginManifest.id)
            .filter(
                PluginManifest.plugin_id == candidate,
                (PluginManifest.yard_id == yard_id) | (PluginManifest.yard_id.is_(None)),
            )
            .first()
        )
        if exists is None:
            return candidate
        index += 1
        candidate = f"{base}-{index}"
        if index > 50:
            raise AppError("IMPORT_CONFLICT", "同名插件过多，无法生成唯一标识。", http_status=409)


def _build_skill_definition(
    *,
    name: str,
    description: str,
    body: str,
    meta: dict,
    filename: str,
) -> dict:
    sections = extract_sections(body)
    prompt_text = section_text(sections, ("purpose", "instruction", "prompt", "说明", "步骤"))
    if not prompt_text:
        prompt_text = body.strip()
    prompt = truncate(prompt_text, 1500)
    steps = extract_list_items(prompt_text, limit=8)
    if not steps:
        steps = extract_list_items(body, limit=8)
    raw_meta = meta.get("metadata")
    meta_meta: dict = raw_meta if isinstance(raw_meta, dict) else {}
    version = str(meta_meta.get("version") or "1.0.0")
    return {
        "name": name,
        "description": truncate(description, 500),
        "prompt": prompt,
        "steps": steps,
        "source_url": f"local://{filename}",
        "license": str(meta.get("license") or "unknown"),
        "attribution": "本地导入，内容由用户提供。",
        "origin": "local_import",
        "original_filename": filename,
        "version": version,
    }


def import_skill_file(db: Session, ctx: YardContext, filename: str, data: bytes | str) -> dict:
    """导入 .md/.markdown/.txt 技能文件，落地 MarketItem + Skill（默认待审）。"""
    raw_name = (filename or "").strip()
    ext = Path(raw_name).suffix.lower()
    if ext not in ALLOWED_SKILL_EXT:
        raise AppError(
            "IMPORT_TYPE_UNSUPPORTED",
            "技能文件仅支持 .md/.markdown/.txt。",
            http_status=422,
            details={"filename": raw_name},
        )
    content = _as_bytes(data)
    if len(content) > MAX_SKILL_BYTES:
        raise AppError("IMPORT_TOO_LARGE", "技能文件超过 256KB。", http_status=413)
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as e:
        raise AppError("IMPORT_INVALID", "文件需为 UTF-8 文本。", http_status=400) from e

    meta, body = parse_frontmatter(text)
    meta = meta if isinstance(meta, dict) else {}
    stem = filename_stem(raw_name)
    base_code = slugify(stem, fallback="imported-skill")
    name = str(meta.get("name") or "").strip() or first_heading(body) or stem or base_code
    description = str(meta.get("description") or "").strip() or first_paragraph(body)
    definition = _build_skill_definition(
        name=name,
        description=description,
        body=body,
        meta=meta,
        filename=raw_name,
    )
    # 导入内容一律待审：文件自带 builtin/verified 标记不可信，否则导入者
    # 可凭 frontmatter 绕过专家审核直接发布提示词。
    code = unique_skill_code(db, ctx.yard_id, base_code)
    item_key = f"local:skill:{code}"
    now = datetime.now(UTC)
    definition["market_item_key"] = item_key

    item = MarketItem(
        item_key=item_key,
        item_type="skill",
        source="local",
        code=code,
        name=name,
        version=definition["version"],
        description=definition["description"],
        source_url=definition["source_url"],
        license=definition["license"],
        author=str(meta.get("author") or "") or None,
        risk_level="low",
        status="available",
        definition_json=definition,
        manifest_json=None,
        metadata_json={
            "original_filename": raw_name,
            "size": len(content),
            "imported_at": now.isoformat(),
            "imported_by": ctx.user_id,
        },
        verified=False,
    )
    db.add(item)
    db.flush()

    skill = create_skill(
        db,
        ctx,
        code,
        definition["version"],
        definition,
        created_from="market_import",
        request_id=ctx.request_id,
    )
    skill.status = "pending_review"
    db.flush()

    return {
        "item_key": item_key,
        "item_type": "skill",
        "source": "local",
        "code": code,
        "name": name,
        "version": definition["version"],
        "status": item.status,
        "skill_id": skill.id,
        "skill_status": skill.status,
        "verified": False,
    }


def _safe_zip_path(name: str) -> None:
    if not name or "\x00" in name:
        raise AppError("PLUGIN_IMPORT_UNSAFE", "压缩包内含非法文件名。", http_status=422)
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("~"):
        raise AppError(
            "PLUGIN_IMPORT_UNSAFE",
            "压缩包不允许绝对路径。",
            http_status=422,
            details={"path": name},
        )
    parts = PurePosixPath(normalized).parts
    if any(part == ".." for part in parts):
        raise AppError(
            "PLUGIN_IMPORT_UNSAFE",
            "压缩包不允许 .. 路径穿越。",
            http_status=422,
            details={"path": name},
        )
    if parts and ":" in parts[0]:
        raise AppError("PLUGIN_IMPORT_UNSAFE", "压缩包不允许盘符路径。", http_status=422)


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def _read_zip_entries(data: bytes) -> tuple[dict[str, bytes], list[str]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError, ValueError) as e:
        raise AppError("PLUGIN_IMPORT_INVALID", "文件不是有效的 zip 包。", http_status=400) from e
    files: dict[str, bytes] = {}
    names: list[str] = []
    total = 0
    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            _safe_zip_path(info.filename)
            if _is_symlink(info):
                raise AppError(
                    "PLUGIN_IMPORT_UNSAFE",
                    "压缩包不允许符号链接。",
                    http_status=422,
                    details={"path": info.filename},
                )
            ext = PurePosixPath(info.filename).suffix.lower()
            if ext in EXECUTABLE_EXT or ext not in ALLOWED_PLUGIN_EXT:
                raise AppError(
                    "PLUGIN_IMPORT_FORBIDDEN_FILE",
                    f"插件包不允许包含该类型文件：{info.filename}",
                    http_status=422,
                    details={"path": info.filename},
                )
            # 流式读取并按全局预算截断：不信任 file_size，避免压缩炸弹一次性占满内存
            budget = MAX_PLUGIN_UNCOMPRESSED - total
            chunks: list[bytes] = []
            read = 0
            try:
                with archive.open(info) as stream:
                    while True:
                        chunk = stream.read(min(65536, budget - read + 1))
                        if not chunk:
                            break
                        read += len(chunk)
                        if read > budget:
                            raise AppError(
                                "IMPORT_TOO_LARGE", "插件解压后内容超过 5MB。", http_status=413
                            )
                        chunks.append(chunk)
            except AppError:
                raise
            except Exception as e:  # 含加密包/损坏压缩流，统一转为可读错误
                raise AppError("PLUGIN_IMPORT_INVALID", "压缩包读取失败。", http_status=400) from e
            total += read
            files[info.filename] = b"".join(chunks)
            names.append(info.filename)
    return files, names


def _find_manifest(files: dict[str, bytes]) -> tuple[str, dict]:
    candidates = sorted(
        (path for path in files if PurePosixPath(path).name == "manifest.json"),
        key=lambda p: (len(PurePosixPath(p).parts), p),
    )
    if not candidates:
        raise AppError("PLUGIN_MANIFEST_INVALID", "插件包缺少 manifest.json。", http_status=422)
    path = candidates[0]
    raw = files[path]
    if len(raw) > MAX_MANIFEST_BYTES:
        raise AppError("IMPORT_TOO_LARGE", "manifest.json 超过 512KB。", http_status=413)
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise AppError(
            "PLUGIN_MANIFEST_INVALID", "manifest.json 不是合法 UTF-8 JSON。", http_status=422
        ) from e
    if not isinstance(manifest, dict):
        raise AppError("PLUGIN_MANIFEST_INVALID", "manifest 必须是 JSON 对象。", http_status=422)
    return path, manifest


def import_plugin_zip(db: Session, ctx: YardContext, filename: str, data: bytes | str) -> dict:
    """导入插件 zip：校验 manifest 与文件白名单，默认待审。"""
    content = _as_bytes(data)
    if len(content) > MAX_PLUGIN_BYTES:
        raise AppError("IMPORT_TOO_LARGE", "插件包超过 2MB。", http_status=413)
    files, names = _read_zip_entries(content)
    manifest_path, manifest = _find_manifest(files)
    validate_manifest(manifest, source=manifest_path)

    base_id = slugify(str(manifest.get("plugin_id") or ""), fallback="imported-plugin")
    plugin_id = unique_plugin_id(db, ctx.yard_id, base_id)
    manifest["plugin_id"] = plugin_id
    declared = str(manifest.get("checksum") or "")
    computed = compute_checksum(manifest)
    checksum_ok = (not declared) or declared == computed
    manifest["checksum"] = declared or computed

    now = datetime.now(UTC)
    code = slugify(plugin_id, fallback="imported-plugin")
    item_key = f"local:plugin:{code}"
    if db.query(MarketItem).filter(MarketItem.item_key == item_key).first() is not None:
        # item_key 全局唯一：插件编码冲突时用唯一后缀兜底
        suffix = 2
        while (
            db.query(MarketItem)
            .filter(MarketItem.item_key == f"local:plugin:{code}-{suffix}")
            .first()
            is not None
        ):
            suffix += 1
        code = f"{code}-{suffix}"
        item_key = f"local:plugin:{code}"

    item = MarketItem(
        item_key=item_key,
        item_type="plugin",
        source="local",
        code=code,
        name=str(manifest.get("name") or plugin_id),
        version=str(manifest.get("version") or "1.0.0"),
        description=str(manifest.get("description") or ""),
        source_url=f"local://{filename}",
        license=str(manifest.get("license") or "") or None,
        author=str(manifest.get("author") or "") or None,
        risk_level="medium",
        status="available",
        definition_json=None,
        manifest_json=manifest,
        metadata_json={
            "original_filename": filename,
            "files": names,
            "size": len(content),
            "imported_at": now.isoformat(),
            "imported_by": ctx.user_id,
        },
        verified=False,
    )
    db.add(item)
    db.flush()

    record = PluginManifest(
        plugin_id=plugin_id,
        yard_id=ctx.yard_id,
        version=str(manifest.get("version")),
        type=str(manifest.get("type")),
        status="pending_review",
        manifest_json=manifest,
        checksum=manifest["checksum"],
        checksum_ok=checksum_ok,
        installed_at=now,
    )
    db.add(record)
    db.flush()

    return {
        "item_key": item_key,
        "item_type": "plugin",
        "source": "local",
        "code": code,
        "name": item.name,
        "version": item.version,
        "status": item.status,
        "plugin_id": plugin_id,
        "plugin_status": record.status,
        "checksum_ok": checksum_ok,
        "files": names,
    }


__all__ = [
    "ALLOWED_PLUGIN_EXT",
    "MAX_PLUGIN_BYTES",
    "MAX_SKILL_BYTES",
    "import_plugin_zip",
    "import_skill_file",
    "unique_plugin_id",
    "unique_skill_code",
]
