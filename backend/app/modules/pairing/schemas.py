"""配对模块请求体模型。"""

from pydantic import BaseModel, Field


class PairExchangeRequest(BaseModel):
    """设备侧交换请求：扫码得到的配对码明文 + 设备自报名称。"""

    # 配对码为 secrets.token_urlsafe(16)（22 字符）；放宽长度下限只做防呆
    code: str = Field(min_length=8, max_length=64, description="扫码得到的配对码明文")
    device_name: str = Field(min_length=1, max_length=100, description="设备自报名称（展示用）")
