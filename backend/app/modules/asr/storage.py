"""ASR 模型目录：跨平台同一份 ONNX 文件，存储位置随操作系统变化。

解析顺序：
1. ``settings.asr_model_dir``（环境变量 ``ASR_MODEL_DIR``，桌面壳/运维可显式指定）；
2. macOS：``~/Library/Application Support/tech-yard/models/asr``；
3. Windows：``%LOCALAPPDATA%\\tech-yard\\models\\asr``；
4. Linux：``$XDG_DATA_HOME/tech-yard/models/asr``（缺省 ``~/.local/share``）。

模型目录与安装包解耦：升级应用不会重复下载模型，卸载也不会删掉用户数据目录
（是否需要清理由用户在「语音转写」里显式删除）。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from app.core.config import settings

APP_DIR_NAME = "tech-yard"


def models_root() -> Path:
    override = (settings.asr_model_dir or os.environ.get("ASR_MODEL_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    elif os.name == "nt":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        base = Path(local) / APP_DIR_NAME
    else:
        xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        base = Path(xdg) / APP_DIR_NAME
    return base / "models" / "asr"


def model_dir(model_id: str) -> Path:
    """模型目录。model_id 只来自内置清单，不接受用户输入拼接。"""
    return models_root() / model_id


def marker_path(model_id: str) -> Path:
    return model_dir(model_id) / ".verified.json"


def remove_model_dir(model_id: str) -> None:
    """删除模型目录：Windows 下文件被占用时短暂重试。"""
    target = model_dir(model_id)
    if not target.exists():
        return
    shutil.rmtree(target, ignore_errors=False)


def free_bytes() -> int:
    root = models_root()
    root.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(root).free
