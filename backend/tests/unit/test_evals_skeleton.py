"""评估集骨架冒烟（W4）：golden jsonl 结构合法 + run_evals.py --help 可用。

只验结构，不打真实网络、不连数据库（真跑是 scripts/run_evals.py 的人工动作）。
"""

import json
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
GOLDEN = BACKEND_DIR / "evals" / "golden_v1.jsonl"
SCRIPT = BACKEND_DIR / "scripts" / "run_evals.py"


def test_golden_v1_jsonl_structure():
    assert GOLDEN.is_file(), f"评估集缺失：{GOLDEN}"
    rows = [
        json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(rows) >= 6
    seen_ids: set[str] = set()
    for row in rows:
        for key in ("id", "agent_code", "question", "expect_keywords", "note"):
            assert key in row, f"{row.get('id')} 缺字段 {key}"
        assert row["id"] not in seen_ids
        seen_ids.add(row["id"])
        assert isinstance(row["agent_code"], str) and row["agent_code"]
        assert isinstance(row["question"], str) and row["question"]
        assert isinstance(row["expect_keywords"], list) and row["expect_keywords"]
        assert all(isinstance(kw, str) and kw for kw in row["expect_keywords"])


def test_run_evals_help_without_env(monkeypatch):
    """--help 必须在零环境准备下可用（重量导入都在 main() 之后）。"""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(BACKEND_DIR),
    )
    assert proc.returncode == 0, proc.stderr
    assert "评估集" in proc.stdout
