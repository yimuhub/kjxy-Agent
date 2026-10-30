"""语音转写（ASR）模块：本机离线模型 + 在线转写通道。

结构：
- ``catalog``：内置模型清单（跨平台共用同一份 ONNX 文件）；
- ``storage``：按操作系统解析模型目录（macOS/Windows/Linux 一致口径）；
- ``local_engine``：sherpa-onnx 懒加载与识别（纯 CPU，无 GPU 依赖）；
- ``downloader``：断点续传 + SHA256 校验 + 原子落盘；
- ``service``：统一状态机与「本机优先、在线兜底」的路由；
- ``router``：``/asr/*`` 状态、下载、删除、测试端点。
"""
