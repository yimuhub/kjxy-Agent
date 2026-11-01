"""``.kjxpack`` 包格式与安全装载（v3.35）。

包结构（zip）::

    manifest.json     包级清单（含 kind/code/version/发布者签名）
    checksums.json    {"algorithm":"sha256","files":{<name>:<hex>}}
    SIGNATURE         base64url(Ed25519(canonical_json(checksums.json)))
    definition.json   kind=skill：SkillSpec 严格形态
    plugin.json       kind=plugin：插件 manifest（声明式，无执行能力）

安全口径（全部失败即拒绝，不做「尽力而为」）：
- 拒绝绝对路径、``..``、盘符、符号链接、加密条目；
- 单包 ≤50MB、文件数 ≤1000、单文件压缩比 ≤100；
- 逐文件写入临时目录后再读回，杜绝 zip slip；
- 下载层必须先核对整包 SHA-256，本模块再核对包内 checksums 与发布者签名；
- 技能必须通过 ``SkillSpec`` 校验与 ``prepublish.static_checks``（error 一票否决）；
- 插件只允许声明式 workflow（拒绝 python/script/exec/shell 等可执行字段）。
"""

from __future__ import annotations

import binascii
import hashlib
import io
import json
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from app.core.errors import AppError
from app.modules.evolution.prepublish import static_checks
from app.modules.library import crypto
from app.modules.market.skill_template import SkillSpec
from app.modules.plugins.service import validate_manifest

MAX_PACKAGE_BYTES = 50 * 1024 * 1024
MAX_FILES = 1000
MAX_COMPRESSION_RATIO = 100
MAX_JSON_BYTES = 2 * 1024 * 1024
SUPPORTED_KINDS = frozenset({"skill", "plugin"})
PACKAGE_MANIFEST = "manifest.json"
CHECKSUMS_FILE = "checksums.json"
SIGNATURE_FILE = "SIGNATURE"

#: 插件包中一律拒绝的键名（按现有白名单校验，不新增执行能力）
FORBIDDEN_PLUGIN_KEYS = frozenset(
    {
        "python",
        "py",
        "script",
        "scripts",
        "exec",
        "execve",
        "shell",
        "bash",
        "cmd",
        "command",
        "subprocess",
        "entry_script",
        "runtime",
        "binary",
        "wasm",
    }
)
MAX_PLUGIN_STEPS = 8


def _invalid(message: str, *, details: dict | None = None, code: str = "LIBRARY_PACKAGE_INVALID"):
    return AppError(code, message, http_status=422, details=details or {})


def _json_load(files: dict[str, bytes], name: str) -> Any:
    raw = files.get(name)
    if raw is None:
        raise _invalid(f"包内缺少 {name}。", details={"file": name})
    if len(raw) > MAX_JSON_BYTES:
        raise AppError(
            "LIBRARY_PACKAGE_TOO_LARGE",
            f"{name} 超过 2MB。",
            http_status=413,
            details={"file": name},
        )
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise _invalid(f"{name} 不是合法 UTF-8 JSON。", details={"file": name}) from e


def _safe_name(name: str) -> None:
    if not name or "\x00" in name:
        raise _invalid("压缩包内含非法文件名。")
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("~"):
        raise _invalid(
            "压缩包不允许绝对路径。",
            details={"path": name},
            code="LIBRARY_PACKAGE_UNSAFE",
        )
    parts = PurePosixPath(normalized).parts
    if any(part == ".." for part in parts):
        raise _invalid(
            "压缩包不允许 .. 路径穿越。",
            details={"path": name},
            code="LIBRARY_PACKAGE_UNSAFE",
        )
    if parts and ":" in parts[0]:
        raise _invalid(
            "压缩包不允许盘符路径。",
            details={"path": name},
            code="LIBRARY_PACKAGE_UNSAFE",
        )


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def _compression_ratio(info: zipfile.ZipInfo) -> float:
    if info.file_size <= 0:
        return 0.0
    if info.compress_size <= 0:
        return float(info.file_size)
    return info.file_size / info.compress_size


def extract_package(data: bytes) -> dict[str, bytes]:
    """安全解压到临时目录并读回：返回 {包内相对路径: bytes}。"""
    if not isinstance(data, bytes | bytearray) or not data:
        raise _invalid("包内容为空或不是字节串。")
    if len(data) > MAX_PACKAGE_BYTES:
        raise AppError(
            "LIBRARY_PACKAGE_TOO_LARGE",
            "安装包超过 50MB。",
            http_status=413,
            details={"size": len(data)},
        )
    try:
        archive = zipfile.ZipFile(io.BytesIO(bytes(data)))
    except (zipfile.BadZipFile, OSError, ValueError) as e:
        raise _invalid("文件不是有效的 .kjxpack（zip）包。") from e

    files: dict[str, bytes] = {}
    total = 0
    try:
        with archive, tempfile.TemporaryDirectory(prefix="kjxpack-") as tmp:
            root = Path(tmp).resolve()
            # 目录条目也要过路径校验：恶意包可能只放 "../" 目录试探解压器
            for entry in archive.infolist():
                _safe_name(entry.filename)
            infos = [info for info in archive.infolist() if not info.is_dir()]
            if len(infos) > MAX_FILES:
                raise _invalid(f"包内文件数超过 {MAX_FILES}。", code="LIBRARY_PACKAGE_UNSAFE")
            for info in infos:
                _safe_name(info.filename)
                if _is_symlink(info):
                    raise _invalid(
                        "压缩包不允许符号链接。",
                        details={"path": info.filename},
                        code="LIBRARY_PACKAGE_UNSAFE",
                    )
                if _compression_ratio(info) > MAX_COMPRESSION_RATIO:
                    raise _invalid(
                        f"压缩比异常（>{MAX_COMPRESSION_RATIO}）：{info.filename}",
                        details={"path": info.filename},
                        code="LIBRARY_PACKAGE_UNSAFE",
                    )
                budget = MAX_PACKAGE_BYTES - total
                if info.file_size > budget:
                    raise AppError(
                        "LIBRARY_PACKAGE_TOO_LARGE",
                        "解压后总大小超过 50MB。",
                        http_status=413,
                        details={"path": info.filename},
                    )
                target = (root / info.filename).resolve()
                if root not in target.parents and target != root:
                    raise _invalid(
                        "解压路径逃逸出临时目录。",
                        details={"path": info.filename},
                        code="LIBRARY_PACKAGE_UNSAFE",
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                try:
                    with archive.open(info) as stream, target.open("wb") as out:
                        while True:
                            chunk = stream.read(min(65536, budget - written + 1))
                            if not chunk:
                                break
                            written += len(chunk)
                            if written > budget:
                                raise AppError(
                                    "LIBRARY_PACKAGE_TOO_LARGE",
                                    "解压后总大小超过 50MB。",
                                    http_status=413,
                                    details={"path": info.filename},
                                )
                            out.write(chunk)
                except AppError:
                    raise
                except RuntimeError as e:
                    # 加密条目 / 损坏流：统一按非法包处理
                    raise _invalid(
                        "压缩包读取失败（含加密或损坏条目）。",
                        details={"path": info.filename},
                    ) from e
                except (zipfile.BadZipFile, OSError) as e:
                    raise _invalid("压缩包读取失败。", details={"path": info.filename}) from e
                total += written
                files[info.filename] = target.read_bytes()
    except zipfile.BadZipFile as e:
        raise _invalid("压缩包读取失败。") from e
    return files


def _checksum_entries(payload: Any) -> dict[str, str]:
    if isinstance(payload, dict) and isinstance(payload.get("files"), dict):
        entries = payload["files"]
    elif isinstance(payload, dict):
        entries = {
            key: value for key, value in payload.items() if key not in {"algorithm", "generated_at"}
        }
    else:
        raise _invalid("checksums.json 必须是 JSON 对象。")
    if not entries:
        raise _invalid("checksums.json 未登记任何文件。")
    return {str(name): str(digest).strip().lower() for name, digest in entries.items()}


def _verify_signature_file(files: dict[str, bytes], checksums: dict, public_key_b64: str) -> None:
    raw = files.get(SIGNATURE_FILE)
    if raw is None:
        raise _invalid(f"包内缺少 {SIGNATURE_FILE}。", details={"file": SIGNATURE_FILE})
    try:
        signature = raw.decode("ascii").strip()
    except UnicodeDecodeError as e:
        raise _invalid("SIGNATURE 不是 base64url ASCII 文本。") from e
    payload = crypto.canonical_json(checksums).encode("utf-8")
    if not crypto.verify_detached(payload, signature, public_key_b64):
        raise AppError(
            "LIBRARY_SIGNATURE_INVALID",
            "包内 checksums.json 的发布者签名无效。",
            http_status=422,
        )


def _assert_declarative(value: Any, path: str = "") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).strip().lower()
            if lowered in FORBIDDEN_PLUGIN_KEYS:
                raise _invalid(
                    f"官方插件包不允许可执行字段：{path}{key}",
                    details={"field": f"{path}{key}"},
                )
            _assert_declarative(item, path=f"{path}{key}.")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _assert_declarative(item, path=f"{path}[{index}].")


def _validate_plugin(definition: dict, manifest: dict) -> None:
    try:
        validate_manifest(definition, source="plugin.json")
    except AppError as e:
        raise _invalid(f"plugin.json 校验失败：{e.message}", details=e.details) from e
    if definition.get("entry_point") not in (None, ""):
        raise _invalid("官方插件仅允许声明式定义，entry_point 必须为空。")
    _assert_declarative(definition)
    if str(definition.get("version")) != str(manifest.get("version")):
        raise _invalid("plugin.json 版本与包清单不一致。")
    if str(definition.get("plugin_id")) != str(manifest.get("code")):
        raise _invalid("plugin.json 的 plugin_id 必须与包清单 code 一致。")
    steps = definition.get("steps")
    if steps is not None:
        if not isinstance(steps, list) or len(steps) > MAX_PLUGIN_STEPS:
            raise _invalid(f"声明式 workflow steps 必须是不超过 {MAX_PLUGIN_STEPS} 条的数组。")
        for step in steps:
            if not isinstance(step, dict) or step.get("kind") not in ("tool", "llm"):
                raise _invalid("声明式 workflow 步骤 kind 只能是 tool 或 llm。")


def _validate_skill(definition: dict, manifest: dict) -> None:
    try:
        spec = SkillSpec.model_validate(definition)
    except Exception as e:  # pydantic ValidationError，避免 CLI 侧重复依赖
        raise _invalid(f"definition.json 不是合法 SkillSpec：{e}") from e
    if str(spec.code) != str(manifest.get("code")):
        raise _invalid("definition.json 的 code 与包清单不一致。")
    if str(spec.version) != str(manifest.get("version")):
        raise _invalid("definition.json 的 version 与包清单不一致。")
    blocking = [
        check
        for check in static_checks(definition)
        if check["severity"] == "error" and not check["passed"]
    ]
    if blocking:
        raise _invalid(
            "definition.json 未通过静态质量检查。",
            details={"issues": [check["code"] for check in blocking]},
        )


def load_package(data: bytes, public_key_b64: str) -> dict[str, Any]:
    """验签 + 校验和 + 类型规则；返回 manifest/definition/files/checksums/sha256。"""
    files = extract_package(data)
    manifest = _json_load(files, PACKAGE_MANIFEST)
    if not isinstance(manifest, dict):
        raise _invalid("manifest.json 必须是 JSON 对象。")
    checksums = _json_load(files, CHECKSUMS_FILE)
    entries = _checksum_entries(checksums)

    verification = crypto.verify_manifest(manifest, public_key_b64)
    if not verification["valid"]:
        raise AppError(
            "LIBRARY_SIGNATURE_INVALID",
            f"包清单签名无效（{verification['reason']}）。",
            http_status=422,
            details={"reason": verification["reason"]},
        )
    if int(manifest.get("schema_version") or 0) != 1:
        raise _invalid("包清单 schema_version 必须为 1。")

    _verify_signature_file(files, checksums, public_key_b64)
    checksum_result = crypto.verify_checksums(files, entries)
    if not checksum_result["valid"]:
        raise AppError(
            "LIBRARY_CHECKSUM_MISMATCH",
            "包内文件校验和不匹配。",
            http_status=422,
            details={
                "mismatched": checksum_result["mismatched"],
                "missing": checksum_result["missing"],
            },
        )
    # 除三个元数据文件外，任何未登记文件都视为夹带
    unlisted = sorted(
        name
        for name in files
        if name not in entries
        and name
        not in {
            PACKAGE_MANIFEST,
            CHECKSUMS_FILE,
            SIGNATURE_FILE,
        }
    )
    if unlisted:
        raise _invalid("包内存在未登记校验和的文件。", details={"files": unlisted})

    kind = str(manifest.get("kind") or "").strip().lower()
    if kind not in SUPPORTED_KINDS:
        raise AppError(
            "LIBRARY_PACKAGE_UNSUPPORTED",
            f"不支持的包类型：{kind or '(空)'}",
            http_status=422,
        )
    payload_name = "definition.json" if kind == "skill" else "plugin.json"
    definition = _json_load(files, payload_name)
    if not isinstance(definition, dict):
        raise _invalid(f"{payload_name} 必须是 JSON 对象。")
    if kind == "skill":
        _validate_skill(definition, manifest)
    else:
        _validate_plugin(definition, manifest)
    return {
        "kind": kind,
        "manifest": manifest,
        "definition": definition,
        "files": files,
        "checksums": checksums,
        "payload_file": payload_name,
        "sha256": hashlib.sha256(bytes(data)).hexdigest(),
        "key_id": verification["key_id"],
    }


def _zip_write(archive: zipfile.ZipFile, name: str, payload: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, payload)


def build_package(*, manifest: dict, definition: dict, seed: bytes) -> bytes:
    """按发布者种子生成 .kjxpack（CLI 与测试共用同一格式）。"""
    kind = str(manifest.get("kind") or "").strip().lower()
    if kind not in SUPPORTED_KINDS:
        raise _invalid(f"不支持的包类型：{kind or '(空)'}")
    for field in ("code", "version", "name"):
        if not str(manifest.get(field) or "").strip():
            raise _invalid(f"包清单缺少 {field}。")
    if kind == "skill" and str(definition.get("code")) != str(manifest["code"]):
        raise _invalid("definition.code 与包清单 code 不一致。")
    if kind == "plugin" and str(definition.get("plugin_id")) != str(manifest["code"]):
        raise _invalid("plugin.json 的 plugin_id 必须与包清单 code 一致。")

    payload_name = "definition.json" if kind == "skill" else "plugin.json"
    payload = json.dumps(definition, ensure_ascii=False, sort_keys=True).encode("utf-8")
    checksums = {
        "algorithm": "sha256",
        "files": {payload_name: hashlib.sha256(payload).hexdigest()},
    }
    checksums_bytes = json.dumps(
        checksums, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    manifest_body = {
        "schema_version": 1,
        "kind": kind,
        **{
            key: value
            for key, value in manifest.items()
            if key not in {"signature", "schema_version"}
        },
    }
    signed = crypto.sign_manifest(manifest_body, seed)
    signature = crypto.sign_canonical(checksums, seed).encode("ascii")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        _zip_write(
            archive,
            PACKAGE_MANIFEST,
            json.dumps(signed, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        )
        _zip_write(archive, CHECKSUMS_FILE, checksums_bytes)
        _zip_write(archive, SIGNATURE_FILE, signature)
        _zip_write(archive, payload_name, payload)
    return buffer.getvalue()


def package_digest(data: bytes) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def decode_signature_text(raw: bytes) -> str:
    try:
        return raw.decode("ascii").strip()
    except (UnicodeDecodeError, binascii.Error) as e:  # pragma: no cover - 防御
        raise _invalid("SIGNATURE 不是 base64url ASCII 文本。") from e


__all__ = [
    "CHECKSUMS_FILE",
    "MAX_FILES",
    "MAX_PACKAGE_BYTES",
    "PACKAGE_MANIFEST",
    "SIGNATURE_FILE",
    "SUPPORTED_KINDS",
    "build_package",
    "extract_package",
    "load_package",
    "package_digest",
]
