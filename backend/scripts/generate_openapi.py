"""生成 contracts/openapi.yaml（从 FastAPI 应用反向导出）。

用法（仓库根目录）：
    .venv/bin/python backend/scripts/generate_openapi.py

CI 的 drift 检查（.github/workflows/ci.yml）用同一脚本重生成并与已提交版本
diff，不一致即失败（规范 §7.6：代码与 OpenAPI 不一致时禁止合并）。

注意：本脚本只做"导出"，不手工编辑 contracts/openapi.yaml；接口变更必须先改
代码/Schema，再跑本脚本更新契约，随同一 PR 提交。
"""

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# 测试环境一致的无网络配置（与 backend/tests/conftest.py 对齐）
os.environ.setdefault("APP_SECRET_KEY", "openapi-gen-secret-min-32-chars")
os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("EMBEDDING_PROVIDER", "mock")
os.environ.setdefault("POLICY_MODULE_ENABLED", "true")
os.environ.setdefault("WORKER_SYNC_FALLBACK", "false")

sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.main import create_app  # noqa: E402

import yaml  # noqa: E402


def main() -> int:
    app = create_app()  # 不触发 lifespan（不连库）
    spec = app.openapi()
    spec["info"]["description"] = (
        "科技小院多智能体自生长开发平台 API 契约（由 FastAPI 应用生成，勿手改）。"
        "生成：`python backend/scripts/generate_openapi.py`"
    )
    out_path = REPO_ROOT / "contracts" / "openapi.yaml"
    text = (
        "# =============================================================\n"
        "# 本文件由 backend/scripts/generate_openapi.py 自动生成，禁止手改。\n"
        "# 接口变更后运行该脚本重新生成，并与代码同 PR 提交。\n"
        "# =============================================================\n"
    )
    out_path.write_text(
        text + yaml.safe_dump(spec, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )
    n_paths = len(spec.get("paths", {}))
    print(f"openapi.yaml written: {out_path} ({n_paths} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
