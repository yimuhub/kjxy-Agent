"""模型下载器：断点续传 + SHA256 校验 + 原子落盘 + 进度查询。

安全边界：
- 下载地址只来自内置清单（``catalog``），不接受用户输入；
- 清单里 ``sha256`` 为空视为「清单未就绪」，拒绝安装；
- 先下到 ``<file>.part``，校验通过后 ``os.replace``，失败不会污染已安装模型。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.core.logging import get_logger
from app.modules.asr import local_engine, storage
from app.modules.asr.catalog import ModelFile, ModelSpec, get_model

log = get_logger(__name__)

#: 下载期允许的最低剩余空间（模型体积之外再留 200MB）
_SPACE_RESERVE = 200 * 1024 * 1024
_CHUNK = 1024 * 256


@dataclass
class DownloadState:
    status: str = "idle"  # idle|downloading|verifying|ready|error|cancelled
    downloaded: int = 0
    total: int = 0
    message: str = ""
    started_at: str = ""
    finished_at: str = ""
    cancel: threading.Event = field(default_factory=threading.Event)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "downloaded": self.downloaded,
            "total": self.total,
            "progress": int(self.downloaded * 100 / self.total) if self.total else 0,
            "message": self.message,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


_LOCK = threading.Lock()
_STATES: dict[str, DownloadState] = {}
_THREADS: dict[str, threading.Thread] = {}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def state_of(model_id: str) -> DownloadState:
    with _LOCK:
        state = _STATES.get(model_id)
        if state is None:
            state = DownloadState()
            _STATES[model_id] = state
        return state


def is_busy(model_id: str) -> bool:
    return state_of(model_id).status in ("downloading", "verifying")


def _manifest_ready(model: ModelSpec) -> bool:
    return all(item.sha256 and item.size > 0 for item in model.files)


def installed_size(model_id: str) -> int:
    model = get_model(model_id)
    if model is None:
        return 0
    base = storage.model_dir(model_id)
    total = 0
    for item in model.files:
        path = base / item.name
        if not path.exists():
            return 0
        total += path.stat().st_size
    return total


def marker_matches(model_id: str) -> bool:
    """校验标记与清单一致（避免换清单后旧文件被当成新模型）。"""
    model = get_model(model_id)
    if model is None:
        return False
    marker = storage.marker_path(model_id)
    if not marker.exists():
        return False
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    expected = {item.name: item.sha256 for item in model.files}
    return data.get("files") == expected and installed_size(model_id) > 0


def is_ready(model_id: str) -> bool:
    return marker_matches(model_id) and not is_busy(model_id)


def _write_marker(model: ModelSpec) -> None:
    marker = storage.marker_path(model.id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {
                "model_id": model.id,
                "files": {item.name: item.sha256 for item in model.files},
                "verified_at": _now(),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_file(item: ModelFile, dest: Path, state: DownloadState, on_bytes: Any) -> None:
    """下载单个文件；``on_bytes(file_bytes)`` 报告该文件当前已落盘字节数。"""
    part = dest.with_name(dest.name + ".part")
    part.parent.mkdir(parents=True, exist_ok=True)
    last_error = ""
    for url in item.mirrors:
        for attempt in range(1, 4):
            if state.cancel.is_set():
                raise _Cancelled()
            try:
                existing = part.stat().st_size if part.exists() else 0
                if existing > item.size:
                    part.unlink(missing_ok=True)
                    existing = 0
                on_bytes(existing)
                headers = {"Range": f"bytes={existing}-"} if existing else {}
                with httpx.Client(
                    timeout=httpx.Timeout(30.0, read=120.0),
                    follow_redirects=True,
                    headers={"User-Agent": "tech-yard-asr/1.0"},
                ) as client:
                    with client.stream("GET", url, headers=headers) as resp:
                        if resp.status_code == 416:  # 已下完
                            existing = part.stat().st_size if part.exists() else 0
                        elif resp.status_code >= 400:
                            raise httpx.HTTPStatusError(
                                f"HTTP {resp.status_code}", request=resp.request, response=resp
                            )
                        else:
                            if resp.status_code == 200 and existing:
                                # 服务端不支持断点：从头重下，已计入的字节要退回
                                part.unlink(missing_ok=True)
                                existing = 0
                                on_bytes(0)
                            with part.open("ab") as fh:
                                for chunk in resp.iter_bytes(_CHUNK):
                                    if state.cancel.is_set():
                                        raise _Cancelled()
                                    if not chunk:
                                        continue
                                    fh.write(chunk)
                                    existing += len(chunk)
                                    on_bytes(existing)
                actual = part.stat().st_size if part.exists() else 0
                if actual != item.size:
                    raise ValueError(f"size mismatch {actual}/{item.size}")
                if item.sha256:
                    digest = _sha256(part)
                    if digest != item.sha256:
                        part.unlink(missing_ok=True)
                        on_bytes(0)
                        raise ValueError("sha256 mismatch")
                os.replace(part, dest)
                return
            except _Cancelled:
                raise
            except Exception as e:  # 换镜像/重试
                last_error = f"{type(e).__name__}: {e}"
                log.warning(
                    "ASR 模型下载失败 model=%s file=%s url=%s attempt=%d err=%s",
                    dest.parent.name,
                    item.name,
                    url,
                    attempt,
                    last_error,
                )
                time.sleep(min(2.0, 0.5 * attempt))
    raise RuntimeError(f"{item.name} 下载失败（{last_error or '未知原因'}）")


class _Cancelled(RuntimeError):
    pass


def _run(model: ModelSpec) -> None:
    state = state_of(model.id)
    state.status = "downloading"
    state.started_at = state.started_at or _now()
    state.total = sum(item.size for item in model.files)
    base = storage.model_dir(model.id)
    done = 0
    for item in model.files:
        part = (base / item.name).with_name(item.name + ".part")
        if part.exists():
            done += part.stat().st_size
    state.downloaded = done
    try:
        completed = 0
        for item in model.files:
            dest = base / item.name
            if dest.exists() and dest.stat().st_size == item.size and item.sha256:
                if _sha256(dest) == item.sha256:
                    completed += item.size
                    state.downloaded = completed
                    continue
            _download_file(
                item,
                dest,
                state,
                lambda n, _c=completed: setattr(state, "downloaded", _c + n),
            )
            completed += item.size
            state.downloaded = completed
        state.status = "verifying"
        _write_marker(model)
        state.status = "ready"
        state.message = "模型已就绪"
        state.finished_at = _now()
        log.info("ASR 模型下载完成 model=%s size=%d", model.id, state.total)
    except _Cancelled:
        state.status = "cancelled"
        state.message = "下载已取消（已下载部分会保留，可续传）"
        state.finished_at = _now()
    except Exception as e:
        state.status = "error"
        state.message = str(e)[:300]
        state.finished_at = _now()
        log.warning("ASR 模型下载失败 model=%s err=%s", model.id, e)
    finally:
        with _LOCK:
            _THREADS.pop(model.id, None)


def start(model_id: str) -> dict[str, Any]:
    """启动下载（幂等：已在下载直接返回当前进度）。"""
    model = get_model(model_id)
    if model is None:
        raise KeyError(model_id)
    if not _manifest_ready(model):
        raise ValueError("模型清单未就绪（缺少校验和），请联系管理员更新版本")
    with _LOCK:
        current = _STATES.get(model_id)
        if current is not None and current.status in ("downloading", "verifying"):
            return current.as_dict()
        free = storage.free_bytes()
        if free < model.size_bytes + _SPACE_RESERVE:
            raise ValueError(
                f"磁盘空间不足：需要约 {model.size_bytes // (1024 * 1024)}MB，"
                f"当前剩余 {free // (1024 * 1024)}MB"
            )
        state = DownloadState(total=model.size_bytes)
        _STATES[model_id] = state
        thread = threading.Thread(
            target=_run, args=(model,), name=f"asr-dl-{model_id}", daemon=True
        )
        _THREADS[model_id] = thread
        thread.start()
        return state.as_dict()


def cancel(model_id: str) -> dict[str, Any]:
    state = state_of(model_id)
    state.cancel.set()
    return state.as_dict()


def remove(model_id: str) -> None:
    """删除模型：先取消下载、卸载引擎（Windows 文件锁），再删目录。"""
    cancel(model_id)
    for _ in range(20):
        if not is_busy(model_id):
            break
        time.sleep(0.1)
    local_engine.unload(model_id)
    last_error: Exception | None = None
    for _ in range(5):
        try:
            storage.remove_model_dir(model_id)
            last_error = None
            break
        except OSError as e:  # Windows 文件仍被占用时短暂重试
            last_error = e
            time.sleep(0.2)
    if last_error is not None:
        raise last_error
    with _LOCK:
        _STATES[model_id] = DownloadState(status="idle", message="模型已删除")


def status_payload(model_id: str) -> dict[str, Any]:
    state = state_of(model_id)
    ready = is_ready(model_id)
    installed = installed_size(model_id)
    payload = state.as_dict()
    if ready and state.status not in ("downloading", "verifying"):
        payload["status"] = "ready"
        payload["message"] = "模型已就绪"
    elif state.status == "idle" and installed > 0 and not ready:
        payload["status"] = "broken"
        payload["message"] = "模型文件校验未通过，请重新下载"
    payload["installed_bytes"] = installed
    payload["ready"] = ready
    return payload


__all__ = [
    "cancel",
    "installed_size",
    "is_busy",
    "is_ready",
    "marker_matches",
    "remove",
    "start",
    "state_of",
    "status_payload",
]
