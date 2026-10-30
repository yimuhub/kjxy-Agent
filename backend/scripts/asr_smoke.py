"""本机语音转写冒烟：三端 CI 共用（macOS / Linux / Windows）。

流程：下载内置模型（已就绪则跳过）→ 校验完成标记 → 用仓库内 5 秒中文音频识别
→ 断言文本包含预期关键词 → 卸载并删除模型 → 断言回到未安装。

用法（CI 里通过 ASR_MODEL_DIR 指到可缓存目录）：
    ASR_MODEL_DIR=/tmp/asr python backend/scripts/asr_smoke.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from app.modules.asr import downloader, local_engine  # noqa: E402
from app.modules.asr.catalog import default_model_id, get_model  # noqa: E402

FIXTURE = REPO / "backend" / "tests" / "fixtures" / "asr-zh.wav"
EXPECTED = "三号田"  # 固定音频，固定模型 → 三端输出一致
TIMEOUT_SECONDS = 900


def fail(message: str) -> None:
    print(f"❌ {message}", file=sys.stderr)
    sys.exit(1)


def wait_ready(model_id: str) -> None:
    started = time.time()
    last = ""
    while time.time() - started < TIMEOUT_SECONDS:
        state = downloader.status_payload(model_id)
        message = f"{state['status']} {state['progress']}% {state['message']}"
        if message != last:
            print(f"   {message}", flush=True)
            last = message
        if downloader.is_ready(model_id):
            return
        if state["status"] in ("error", "broken", "cancelled"):
            fail(f"模型安装失败：{state['message']}")
        time.sleep(2)
    fail("模型下载超时（15 分钟）")


def main() -> None:
    model_id = default_model_id()
    model = get_model(model_id)
    assert model is not None
    print(f"ASR 冒烟：model={model_id} dir={os.environ.get('ASR_MODEL_DIR', '(os default)')}")
    print(f"引擎可用：{local_engine.engine_available()}（{local_engine.engine_note()}）")
    if not local_engine.engine_available():
        fail("sherpa-onnx 未安装（wheel 缺失或平台不支持）")

    if not downloader.is_ready(model_id):
        print("开始下载模型…", flush=True)
        downloader.start(model_id)
        wait_ready(model_id)
    else:
        print("模型已在缓存中，跳过下载")

    data = FIXTURE.read_bytes()
    samples = local_engine.decode_wav(data)
    if len(samples) < local_engine.SAMPLE_RATE:
        fail("测试音频解码异常")
    text, elapsed_ms = local_engine.timed_recognize(model, data)
    print(f"识别结果：{text!r}（{elapsed_ms}ms）")
    if not text:
        fail("识别结果为空")
    if EXPECTED not in text:
        fail(f"识别结果未包含预期关键词：{EXPECTED}")

    print("清理：卸载引擎并删除模型…")
    local_engine.unload(model_id)
    downloader.remove(model_id)
    if downloader.is_ready(model_id) or downloader.installed_size(model_id):
        fail("模型删除后仍有残留")
    print("✅ ASR 冒烟通过")


if __name__ == "__main__":
    main()
