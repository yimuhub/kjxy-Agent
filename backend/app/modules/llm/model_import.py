"""模型批量导入解析与幂等同步（从 supplier_service 拆分，保持对外重导出兼容）。"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.llm.modality import guess_modality, protocol_for_modality
from app.modules.llm.provider import invalidate_supplier_cache
from app.modules.llm.supplier_models import Supplier, SupplierModel
from app.modules.llm.supplier_schemas import (
    SupplierCreate,
    SupplierImportBundle,
    SupplierImportPreview,
    SupplierImportPreviewItem,
    SupplierImportResult,
    SupplierModelCreate,
)

#: URL 导入：响应体硬上限 2MB、最多 3 次重定向（每跳都做 SSRF 校验）
IMPORT_MAX_BYTES = 2 * 1024 * 1024
IMPORT_MAX_REDIRECTS = 3
IMPORT_TIMEOUT_SECONDS = 15

#: 模型行可写字段（导入 upsert 的差集计算口径）
_MODEL_WRITABLE_FIELDS: tuple[str, ...] = (
    "model_key",
    "upstream_model_id",
    "display_name",
    "modality",
    "protocol",
    "input_modalities",
    "output_modalities",
    "capabilities",
    "context_window",
    "max_output_tokens",
    "endpoint_path",
    "pricing",
    "default_params",
    "limits_json",
    "status",
)


def _detect_format(content: str, declared: str | None) -> str:
    if declared:
        return declared
    stripped = content.lstrip()
    if stripped.startswith(("{", "[")):
        return "json"
    first_line = stripped.splitlines()[0] if stripped else ""
    if "," in first_line and "model_key" in first_line.lower():
        return "csv"
    if any(line.strip().endswith(":") for line in stripped.splitlines()[:5]):
        return "yaml"
    return "json"


def _split_tokens(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[;,|/\s]+", value) if part.strip()]
    return []


def _to_float(value: Any) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as e:
        raise AppError("IMPORT_ROW_INVALID", f"价格「{value}」不是合法数字。", 400) from e


def _normalize_import_row(raw: dict[str, Any]) -> dict[str, Any]:
    """把外部目录行（含 CSV 平铺列）归一化成 SupplierModelCreate 的字段。"""
    model_key = str(raw.get("model_key") or raw.get("id") or raw.get("name") or "").strip()
    upstream = str(
        raw.get("upstream_model_id") or raw.get("upstream") or raw.get("model") or model_key
    ).strip()
    modality = str(raw.get("modality") or guess_modality(upstream or model_key)).strip()
    protocol = str(raw.get("protocol") or protocol_for_modality(modality)).strip()
    row: dict[str, Any] = {
        "model_key": model_key,
        "upstream_model_id": upstream,
        "display_name": str(raw.get("display_name") or raw.get("label") or model_key).strip(),
        "modality": modality,
        "protocol": protocol,
    }
    for field in ("endpoint_path", "external_id"):
        if raw.get(field):
            row[field] = str(raw[field])[:200]
    for field in ("context_window", "max_output_tokens"):
        if raw.get(field) not in (None, ""):
            try:
                row[field] = int(raw[field])
            except (TypeError, ValueError) as e:
                raise AppError("IMPORT_ROW_INVALID", f"{field} 必须是整数。", 400) from e
    for field in ("input_modalities", "output_modalities"):
        if raw.get(field):
            row[field] = _split_tokens(raw[field])
    capabilities = raw.get("capabilities")
    if isinstance(capabilities, dict):
        row["capabilities"] = capabilities
    elif capabilities:
        row["capabilities"] = {token: True for token in _split_tokens(capabilities)}
    pricing = raw.get("pricing")
    if isinstance(pricing, dict):
        row["pricing"] = pricing
    else:
        prices: dict[str, Any] = {}
        for src_key, dst_key in (
            ("input_price", "input_per_1k"),
            ("input_per_1k", "input_per_1k"),
            ("output_price", "output_per_1k"),
            ("output_per_1k", "output_per_1k"),
        ):
            value = _to_float(raw.get(src_key))
            if value is not None:
                prices[dst_key] = value
        if raw.get("currency"):
            prices["currency"] = str(raw["currency"])[:8]
        if prices:
            row["pricing"] = prices
    if isinstance(raw.get("default_params"), dict):
        row["default_params"] = raw["default_params"]
    return row


def parse_import_content(content: str, fmt: str | None = None) -> list[dict[str, Any]]:
    """解析导入文本（JSON / YAML / CSV）为原始行列表；结构错直接给中文原因。"""
    text = (content or "").strip()
    if not text:
        raise AppError("IMPORT_EMPTY", "导入内容为空。", 400)
    resolved = _detect_format(text, fmt)
    try:
        if resolved == "json":
            payload = json.loads(text)
            rows = payload.get("models") if isinstance(payload, dict) else payload
            if not isinstance(rows, list):
                raise AppError(
                    "IMPORT_INVALID",
                    'JSON 导入需要模型数组（或 {"models": [...]}）。',
                    400,
                )
        elif resolved == "yaml":
            try:
                import yaml  # type: ignore[import-untyped]
            except ImportError as e:
                raise AppError(
                    "IMPORT_YAML_UNSUPPORTED",
                    "服务器未启用 YAML 解析（缺少 PyYAML），请改用 JSON 或 CSV 导入。",
                    http_status=400,
                ) from e
            payload = yaml.safe_load(text)
            rows = payload.get("models") if isinstance(payload, dict) else payload
            if not isinstance(rows, list):
                raise AppError(
                    "IMPORT_INVALID",
                    "YAML 导入需要模型数组（或 models: [...]）。",
                    400,
                )
        elif resolved == "csv":
            reader = csv.DictReader(io.StringIO(text))
            rows = [dict(item) for item in reader]
        else:  # pragma: no cover
            raise AppError("IMPORT_INVALID", f"不支持的导入格式：{resolved}。", 400)
    except AppError:
        raise
    except Exception as e:  # noqa: BLE001
        raise AppError("IMPORT_INVALID", f"导入内容解析失败：{e}", 400) from e
    out: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            raise AppError("IMPORT_INVALID", "导入内容包含非对象行。", 400)
        out.append(_normalize_import_row(item))
    return out


def _bundle_rows(
    bundle: SupplierImportBundle,
) -> list[tuple[dict[str, Any] | None, str, str | None]]:
    """返回 (行数据, 展示 key, 错误)；结构化与文本模式共用同一分类逻辑。"""
    from pydantic import ValidationError

    if bundle.content:
        raw_rows = parse_import_content(bundle.content, bundle.format)
        out: list[tuple[dict[str, Any] | None, str, str | None]] = []
        for index, raw in enumerate(raw_rows):
            display = str(raw.get("model_key") or f"第 {index + 1} 行")
            try:
                payload = SupplierModelCreate(**raw)
            except ValidationError as e:
                first = e.errors()[0]
                field = ".".join(str(part) for part in first.get("loc", ()))
                out.append((None, display, f"{field}: {first.get('msg', '校验失败')}"))
                continue
            out.append((payload.model_dump(), payload.model_key, None))
        return out
    return [(item.model_dump(), item.model_key, None) for item in bundle.models]


def _diff_model(row: SupplierModel, data: dict[str, Any]) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for field in _MODEL_WRITABLE_FIELDS:
        if field not in data:
            continue
        value = data[field]
        if field == "display_name" and not value:
            value = data.get("model_key")
        if getattr(row, field) != value:
            changes[field] = value
    return changes


def import_models(
    db: Session,
    yard_id: str | None,
    supplier_id: str | None,
    bundle: SupplierImportBundle,
    *,
    dry_run: bool = False,
    is_platform_admin: bool = False,
    source: str = "import",
) -> SupplierImportPreview | SupplierImportResult:
    """严格校验、逐行分类 create/update/skip/conflict/error 的幂等导入。"""
    from app.modules.llm import supplier_service as _svc

    supplier: Supplier | None = None
    supplier_action = "existing"
    if supplier_id:
        supplier = _svc._writable_model_supplier(
            db, yard_id, supplier_id, is_platform_admin=is_platform_admin
        )
    else:
        meta = bundle.supplier
        if not (meta and meta.name and meta.provider_type and meta.base_url and meta.api_key):
            raise AppError(
                "INVALID_REQUEST",
                "导入需要指定供应商，或在导入包中提供 name/provider_type/base_url/api_key "
                "以创建新供应商。",
                http_status=400,
            )
        supplier_action = "create"

    existing_rows: list[SupplierModel] = []
    if supplier is not None:
        existing_rows = (
            db.query(SupplierModel).filter(SupplierModel.supplier_id == supplier.id).all()
        )
    by_key = {row.model_key: row for row in existing_rows}
    by_upstream: dict[str, SupplierModel] = {}
    for existing in existing_rows:
        by_upstream.setdefault(existing.upstream_model_id, existing)

    items: list[SupplierImportPreviewItem] = []
    plans: list[tuple[str, dict[str, Any], SupplierModel | None]] = []
    seen_keys: set[str] = set()
    seen_upstream: set[str] = set()

    for raw, display_key, parse_error in _bundle_rows(bundle):
        if parse_error or raw is None:
            items.append(
                SupplierImportPreviewItem(
                    action="error", model_key=display_key, error=parse_error or "行数据无效"
                )
            )
            continue
        try:
            payload = SupplierModelCreate(**raw)
            data = _svc._model_values(payload)
        except AppError as e:
            items.append(
                SupplierImportPreviewItem(action="error", model_key=display_key, error=e.message)
            )
            continue
        except Exception as e:  # noqa: BLE001
            items.append(
                SupplierImportPreviewItem(action="error", model_key=display_key, error=str(e)[:200])
            )
            continue

        key = data["model_key"]
        upstream = data["upstream_model_id"]
        if key in seen_keys or upstream in seen_upstream:
            items.append(
                SupplierImportPreviewItem(
                    action="conflict",
                    model_key=key,
                    error="导入包内 model_key / upstream_model_id 重复。",
                )
            )
            continue
        seen_keys.add(key)
        seen_upstream.add(upstream)

        row = by_key.get(key)
        if row is None:
            row = by_upstream.get(upstream)
            if row is not None and row.model_key != key:
                if key in by_key and by_key[key] is not row:
                    items.append(
                        SupplierImportPreviewItem(
                            action="conflict",
                            model_key=key,
                            error="model_key 已被该供应商的另一条模型占用。",
                        )
                    )
                    continue
        if row is None:
            plans.append(("create", data, None))
            items.append(SupplierImportPreviewItem(action="create", model_key=key))
            continue
        changes = _diff_model(row, data)
        if changes:
            plans.append(("update", changes, row))
            items.append(SupplierImportPreviewItem(action="update", model_key=key))
        else:
            items.append(SupplierImportPreviewItem(action="skip", model_key=key))

    summary = {"create": 0, "update": 0, "skip": 0, "conflict": 0, "error": 0}
    for item in items:
        summary[item.action] += 1

    if dry_run:
        return SupplierImportPreview(
            items=items,
            summary=summary,
            supplier_action=supplier_action,
            supplier_id=supplier.id if supplier is not None else None,
        )

    if supplier is None and not plans:
        raise AppError("IMPORT_EMPTY", "导入包没有可用的模型行，未创建供应商。", 400)

    if supplier is None:
        meta = bundle.supplier
        if meta is None:
            raise AppError("INVALID_REQUEST", "导入包缺少供应商信息。", http_status=400)
        first_upstream = next(
            (plan[1]["upstream_model_id"] for plan in plans if plan[0] == "create"),
            "placeholder",
        )
        out = _svc.create_supplier(
            db,
            yard_id,
            SupplierCreate(
                name=(meta.name or "imported")[:40],
                provider_type=meta.provider_type,  # type: ignore[arg-type]
                base_url=meta.base_url or "",
                model_id=first_upstream[:100],
                api_key=meta.api_key or "",
                models=None,
                scope=meta.scope,
            ),
            is_platform_admin=is_platform_admin,
        )
        supplier = db.get(Supplier, out.id)
        if supplier is None:  # pragma: no cover
            raise AppError("SUPPLIER_NOT_FOUND", "供应商创建失败。", http_status=500)

    created = updated = 0
    errors: list[str] = []
    for action, data, row in plans:
        if action == "create":
            db.add(
                SupplierModel(
                    supplier_id=supplier.id,
                    source=data.pop("source", source),
                    **data,
                )
            )
            created += 1
        elif row is not None:
            for field, value in data.items():
                setattr(row, field, value)
            updated += 1
    for item in items:
        if item.action == "error":
            errors.append(f"{item.model_key}: {item.error or '校验失败'}")
        elif item.action == "conflict":
            errors.append(f"{item.model_key}: {item.error or '冲突'}")
    if bundle.supplier and bundle.supplier.models:
        supplier.models = bundle.supplier.models.strip()[:2000] or None
    db.flush()
    _svc._sync_supplier_defaults(db, supplier)
    invalidate_supplier_cache(supplier_id=supplier.id)
    return SupplierImportResult(
        supplier_id=supplier.id,
        created=created,
        updated=updated,
        skipped=summary["skip"],
        errors=errors,
    )


async def fetch_import_text(url: str) -> str:
    """按 URL 拉取导入内容：只允许 http/https、每跳 SSRF 校验、2MB 上限。"""
    from app.core.net_guard import check_egress_url

    current = (url or "").strip()
    parsed = urlparse(current)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise AppError("INVALID_REQUEST", "导入地址必须是 http(s) 完整 URL。", 400)
    for hop in range(IMPORT_MAX_REDIRECTS + 1):
        check_egress_url(current, context="supplier_import_url", allow_local=False)
        try:
            async with httpx.AsyncClient(
                timeout=IMPORT_TIMEOUT_SECONDS,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                resp = await client.get(
                    current,
                    headers={"Accept": "application/json, text/yaml, text/csv, text/plain, */*"},
                )
        except httpx.TimeoutException as e:
            raise AppError("IMPORT_FETCH_FAILED", "拉取导入地址超时。", 502) from e
        except httpx.HTTPError as e:
            raise AppError("IMPORT_FETCH_FAILED", "无法连接导入地址。", 502) from e
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("location")
            if not location:
                raise AppError("IMPORT_FETCH_FAILED", "重定向缺少 Location。", 502)
            if hop >= IMPORT_MAX_REDIRECTS:
                raise AppError(
                    "IMPORT_TOO_MANY_REDIRECTS", "导入地址重定向超过 3 次，已中止。", 400
                )
            current = urljoin(current, location)
            continue
        if resp.status_code >= 400:
            raise AppError("IMPORT_FETCH_FAILED", f"导入地址返回 {resp.status_code}。", 502)
        if len(resp.content) > IMPORT_MAX_BYTES:
            raise AppError("IMPORT_TOO_LARGE", "导入内容超过 2MB 上限，已中止。", 413)
        return resp.text
    raise AppError(  # pragma: no cover
        "IMPORT_TOO_MANY_REDIRECTS", "导入地址重定向超过 3 次，已中止。", 400
    )


__all__ = [
    "IMPORT_MAX_BYTES",
    "IMPORT_MAX_REDIRECTS",
    "IMPORT_TIMEOUT_SECONDS",
    "_MODEL_WRITABLE_FIELDS",
    "_bundle_rows",
    "_detect_format",
    "_diff_model",
    "_normalize_import_row",
    "_split_tokens",
    "_to_float",
    "fetch_import_text",
    "import_models",
    "parse_import_content",
]
