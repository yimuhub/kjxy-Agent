"""供应商预置模板清单（从 supplier_service 拆分，保持对外重导出兼容）。"""

from __future__ import annotations

from app.modules.llm.supplier_schemas import ProviderTemplate


def get_provider_templates() -> list[ProviderTemplate]:
    return [
        ProviderTemplate(
            provider_type="deepseek",
            label="DeepSeek",
            base_url="https://api.deepseek.com",
            model_id="deepseek-chat",
            note="OpenAI 兼容接口",
        ),
        ProviderTemplate(
            provider_type="qwen",
            label="通义千问（Qwen）",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model_id="qwen-max",
            note="阿里云百炼 OpenAI 兼容模式",
        ),
        ProviderTemplate(
            provider_type="moonshot",
            label="Moonshot（Kimi）",
            base_url="https://api.moonshot.cn/v1",
            model_id="moonshot-v1-8k",
            note="OpenAI 兼容接口",
        ),
        ProviderTemplate(
            provider_type="openai",
            label="OpenAI",
            base_url="https://api.openai.com/v1",
            model_id="gpt-4o-mini",
            note="官方接口",
        ),
        ProviderTemplate(
            provider_type="openai_compatible",
            label="自定义（OpenAI 兼容）",
            base_url="",
            model_id="",
            note="任意兼容 POST {base_url}/chat/completions 的服务",
        ),
        # ---- v3.26 视觉域：图像/视频供应商与对话供应商分开配置 ----
        ProviderTemplate(
            provider_type="openai_images",
            label="图像生成（OpenAI 兼容）",
            base_url="https://api.openai.com/v1",
            model_id="gpt-image-1",
            note="兼容 POST {base_url}/images/generations；海报/配图走这里，不占用对话模型",
        ),
        ProviderTemplate(
            provider_type="dashscope_image",
            label="图像生成（阿里云百炼）",
            base_url="https://dashscope.aliyuncs.com",
            model_id="wanx2.1-t2i-turbo",
            note="百炼原生异步文生图接口（提交任务后轮询取图），base_url 不要带 /compatible-mode",
        ),
        ProviderTemplate(
            provider_type="openai_video",
            label="视频生成（OpenAI 兼容）",
            base_url="https://api.openai.com/v1",
            model_id="sora-2",
            note="先试 POST {base_url}/videos，404 再试 /video/generations；聚合网关也可用",
        ),
        ProviderTemplate(
            provider_type="dashscope_video",
            label="视频生成（阿里云百炼）",
            base_url="https://dashscope.aliyuncs.com",
            model_id="wan2.2-t2v-plus",
            note="百炼原生异步视频接口（提交任务后轮询），base_url 不要带 /compatible-mode",
        ),
    ]


__all__ = ["get_provider_templates"]
