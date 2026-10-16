"""作物诊断 Agent 模块：基于症状/环境的诊断辅助（非确定诊断）。"""

from app.modules.crop_diagnosis.agent import CropDiagnosisAgent  # noqa: F401 触发全局注册
from app.modules.crop_diagnosis.schemas import DiagnosisInput, DiagnosisOutput
from app.modules.crop_diagnosis.service import diagnose

__all__ = ["CropDiagnosisAgent", "DiagnosisInput", "DiagnosisOutput", "diagnose"]
