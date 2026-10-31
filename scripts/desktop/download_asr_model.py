#!/usr/bin/env python3
"""下载桌面安装包内置的轻量语音模型（可重复执行，校验通过即跳过）。

用法：
    .venv/bin/python scripts/desktop/download_asr_model.py \
        --dest client/desktop/src-tauri/asr-model

产物：<dest>/<model-id>/{model.int8.onnx,tokens.txt,.verified.json}
安装包首启时由 standalone 复制到用户数据目录，用户无需再下载。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.modules.asr.catalog import default_model_id, get_model  # noqa: E402

CHUNK = 1024 * 1024
UA = "techyard-desktop-builder/1.0"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def marker_matches(model_dir: Path, model) -> bool:
    marker = model_dir / ".verified.json"
    if not marker.is_file():
        return False
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    expected = {item.name: item.sha256 for item in model.files}
    if data.get("files") != expected:
        return False
    return all(
        (model_dir / item.name).is_file() and (model_dir / item.name).stat().st_size == item.size
        for item in model.files
    )


def download(item, target: Path) -> None:
    part = target.with_suffix(target.suffix + ".part")
    last_error: Exception | None = None
    for url in item.mirrors:
        try:
            print(f"  下载 {item.name} ← {url}")
            request = urllib.request.Request(url, headers={"User-Agent": UA})
            started = time.time()
            with urllib.request.urlopen(request, timeout=60) as resp, part.open("wb") as out:
                while True:
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
            size = part.stat().st_size
            if item.size and size != item.size:
                raise ValueError(f"大小不符：{size} != {item.size}")
            digest = sha256_of(part)
            if digest != item.sha256:
                raise ValueError(f"sha256 不符：{digest}")
            part.replace(target)
            print(f"    完成 {size / 1024 / 1024:.1f}MB，用时 {time.time() - started:.1f}s")
            return
        except Exception as exc:  # 换镜像重试，全部失败才报错
            last_error = exc
            print(f"    失败：{exc}")
            part.unlink(missing_ok=True)
    raise SystemExit(f"模型文件下载失败：{item.name}（{last_error}）")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dest", required=True, help="资源输出目录（Tauri resources）")
    parser.add_argument("--model", default=default_model_id())
    args = parser.parse_args()

    model = get_model(args.model)
    if model is None:
        raise SystemExit(f"未知模型：{args.model}")
    dest = Path(args.dest).resolve()
    model_dir = dest / model.id
    model_dir.mkdir(parents=True, exist_ok=True)

    if marker_matches(model_dir, model):
        print(f"✅ 内置语音模型已就绪：{model_dir}")
        return

    print(f"准备内置语音模型：{model.name}（{model.size_bytes / 1024 / 1024:.1f}MB）")
    for item in model.files:
        target = model_dir / item.name
        if target.is_file() and target.stat().st_size == item.size:
            if item.sha256 and sha256_of(target) == item.sha256:
                print(f"  复用已存在文件：{item.name}")
                continue
        download(item, target)

    marker = {
        "model_id": model.id,
        "files": {item.name: item.sha256 for item in model.files},
        "verified_at": datetime.now(UTC).isoformat(),
    }
    (model_dir / ".verified.json").write_text(
        json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"✅ 内置语音模型已就绪：{model_dir}")


if __name__ == "__main__":
    main()
