"""插件发现与注册表服务。

- 扫描 ``plugins/`` 目录（可递归）下所有 ``manifest.json``；
- 校验 manifest 必填字段（V3.0 §9.4）与插件类型；
- 校验 checksum，不一致只告警、不阻断注册；
- 注册表 upsert：新插件默认 disabled；重扫保留已有启用状态；
- enable/disable 只改状态，不删业务数据（V3.0 §9.5）。
"""

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.plugins.models import PluginManifest

PLUGIN_TYPES = ("workflow", "knowledge_pack", "domain_pack")

REQUIRED_FIELDS = (
    "plugin_id",
    "name",
    "version",
    "type",
    "api_compatibility",
    "dependencies",
    "permissions",
    "entry_point",
    "config_schema",
    "checksum",
)

STATUS_ENABLED = "enabled"
STATUS_DISABLED = "disabled"


def plugin_dir() -> Path:
    """插件根目录：环境变量 PLUGIN_DIR 优先（测试用），默认仓库根 plugins/。"""
    env = os.environ.get("PLUGIN_DIR")
    if env:
        return Path(env)
    # backend/app/modules/plugins/service.py -> 上 4 级 = 仓库根
    return Path(__file__).resolve().parents[4] / "plugins"


def compute_checksum(manifest: dict) -> str:
    """manifest 内容哈希：排除 checksum 字段本身，规范 JSON 序列化后 sha256。"""
    body = {k: v for k, v in manifest.items() if k != "checksum"}
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_manifest(raw: dict, source: str = "<unknown>") -> dict:
    """校验 manifest 必填字段与类型；不合法抛 AppError（供扫描逐文件收集错误）。"""
    if not isinstance(raw, dict):
        raise AppError(
            "PLUGIN_MANIFEST_INVALID",
            f"manifest 不是合法 JSON 对象：{source}",
            http_status=422,
        )
    missing = [f for f in REQUIRED_FIELDS if f not in raw]
    if missing:
        raise AppError(
            "PLUGIN_MANIFEST_INVALID",
            f"manifest 缺少必填字段：{', '.join(missing)}（{source}）",
            http_status=422,
            details={"missing": missing, "source": source},
        )
    ptype = raw.get("type")
    if ptype not in PLUGIN_TYPES:
        raise AppError(
            "PLUGIN_TYPE_UNSUPPORTED",
            f"不支持的插件类型：{ptype}（{source}），首版仅支持 {', '.join(PLUGIN_TYPES)}",
            http_status=422,
            details={"type": ptype, "source": source},
        )
    for list_field in ("dependencies", "permissions"):
        if not isinstance(raw.get(list_field), list):
            raise AppError(
                "PLUGIN_MANIFEST_INVALID",
                f"manifest 字段 {list_field} 必须是数组（{source}）",
                http_status=422,
            )
    entry_point = raw.get("entry_point")
    if entry_point is not None and not isinstance(entry_point, str):
        raise AppError(
            "PLUGIN_MANIFEST_INVALID",
            f"manifest 字段 entry_point 必须为字符串或 null（{source}）",
            http_status=422,
        )
    if not str(raw.get("plugin_id") or "").strip():
        raise AppError(
            "PLUGIN_MANIFEST_INVALID",
            f"manifest plugin_id 不能为空（{source}）",
            http_status=422,
        )
    return raw


def _find_record(db: Session, plugin_id: str, tenant_id: str | None = None):
    stmt = select(PluginManifest).where(PluginManifest.plugin_id == plugin_id)
    if tenant_id is None:
        stmt = stmt.where(PluginManifest.tenant_id.is_(None))
    else:
        stmt = stmt.where(PluginManifest.tenant_id == tenant_id)
    return db.execute(stmt).scalar_one_or_none()


def list_plugins(
    db: Session, plugin_type: str | None = None, status: str | None = None
) -> list[PluginManifest]:
    stmt = select(PluginManifest).order_by(PluginManifest.plugin_id)
    if plugin_type:
        stmt = stmt.where(PluginManifest.type == plugin_type)
    if status:
        stmt = stmt.where(PluginManifest.status == status)
    return list(db.execute(stmt).scalars().all())


def scan_plugins(db: Session, root: Path | None = None) -> dict:
    """扫描插件目录并同步注册表；返回扫描报告（dict，可直接塞进 ScanReport）。

    - manifest.json 解析/校验失败：记入 errors，不阻断其它插件；
    - checksum 与内容不符：记入 checksum_warnings，照常注册（checksum_ok=False）；
    - 新插件默认 disabled；已存在的只更新 manifest/version/type/checksum，保留状态。
    """
    root = root or plugin_dir()
    report: dict = {
        "discovered": 0,
        "registered": 0,
        "errors": [],
        "checksum_warnings": [],
    }
    if not root.exists():
        report["errors"].append({"path": str(root), "error": "插件目录不存在"})
        return report

    for manifest_path in sorted(root.rglob("manifest.json")):
        rel = str(manifest_path.relative_to(root))
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            report["errors"].append({"path": rel, "error": f"manifest 解析失败：{e}"})
            continue
        try:
            manifest = validate_manifest(raw, source=rel)
        except AppError as e:
            report["errors"].append({"path": rel, "error": e.message})
            continue

        report["discovered"] += 1
        declared = str(manifest.get("checksum") or "")
        actual = compute_checksum(manifest)
        checksum_ok = declared == actual
        if not checksum_ok:
            report["checksum_warnings"].append(
                {
                    "plugin_id": str(manifest["plugin_id"]),
                    "path": rel,
                    "message": (
                        "checksum 与 manifest 内容不一致（声明值与计算值不符），" "已告警并照常注册"
                    ),
                }
            )

        record = _find_record(db, str(manifest["plugin_id"]))
        now = datetime.now(UTC)
        if record is None:
            record = PluginManifest(
                plugin_id=str(manifest["plugin_id"]),
                tenant_id=None,  # 首版：全局插件
                version=str(manifest["version"]),
                type=str(manifest["type"]),
                status=STATUS_DISABLED,
                manifest_json=manifest,
                checksum=declared,
                checksum_ok=checksum_ok,
                installed_at=now,
            )
            db.add(record)
        else:
            record.version = str(manifest["version"])
            record.type = str(manifest["type"])
            record.manifest_json = manifest
            record.checksum = declared
            record.checksum_ok = checksum_ok
            # status 保持不变：重扫不改启用状态
        report["registered"] += 1
    db.flush()
    return report


def get_plugin(db: Session, plugin_id: str) -> PluginManifest:
    record = _find_record(db, plugin_id)
    if record is None:
        raise AppError(
            "PLUGIN_NOT_FOUND",
            f"插件不存在：{plugin_id}",
            http_status=404,
            details={"plugin_id": plugin_id},
        )
    return record


def enable_plugin(db: Session, plugin_id: str) -> PluginManifest:
    """启用插件：只改状态。"""
    record = get_plugin(db, plugin_id)
    record.status = STATUS_ENABLED
    db.flush()
    return record


def disable_plugin(db: Session, plugin_id: str) -> PluginManifest:
    """停用插件：只改状态，不删除业务数据（V3.0 §9.5）。"""
    record = get_plugin(db, plugin_id)
    record.status = STATUS_DISABLED
    db.flush()
    return record


def get_active_domain_packs(db: Session, pack_type: str = "domain_pack") -> list[dict]:
    """返回已启用的领域包 manifest（供业务模块合并规则/配置时调用）。

    简单实现：只返回 manifest_json 列表，业务侧自行决定如何合并。
    """
    stmt = (
        select(PluginManifest)
        .where(PluginManifest.type == pack_type, PluginManifest.status == STATUS_ENABLED)
        .order_by(PluginManifest.plugin_id)
    )
    return [r.manifest_json for r in db.execute(stmt).scalars().all()]


def plugin_to_out(record: PluginManifest) -> dict:
    manifest = record.manifest_json or {}
    return {
        "id": record.id,
        "plugin_id": record.plugin_id,
        "tenant_id": record.tenant_id,
        "version": record.version,
        "type": record.type,
        "status": record.status,
        "name": str(manifest.get("name") or record.plugin_id),
        "description": manifest.get("description"),
        "checksum_ok": record.checksum_ok,
        "installed_at": record.installed_at,
        "created_at": record.created_at,
    }
