"""系统日志读取：尾部截取、级别/关键词过滤与脱敏（离线）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.modules.system import logs as logs_module


@pytest.fixture()
def log_file(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "app.log"
    path.write_text(
        "\n".join(
            [
                "2026-11-01 10:00:00,001 INFO [app.main] [req-1] 服务启动",
                "2026-11-01 10:00:01,002 WARNING [app.modules.commander.service] [req-1] "
                "commander plan unusable, fallback applied",
                "2026-11-01 10:00:02,003 ERROR [app.modules.llm.client] [req-2] "
                "upstream 401 api_key=sk-secret123456 Authorization: Bearer abcdefgh12345678",
                "    Traceback (most recent call last):",
                "2026-11-01 10:00:03,004 INFO [uvicorn.access] [req-3] 200 OK /api/v1/chat",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    engine = tmp_path / "engine.log"
    engine.write_text("", encoding="utf-8")
    monkeypatch.setattr(logs_module, "log_paths", lambda: {"app": path, "engine": engine})
    return path


def test_read_logs_newest_first_and_level_filter(log_file):
    page = logs_module.read_logs(source="app", level="WARNING", limit=50)
    assert [item["level"] for item in page["items"]] == ["ERROR", "WARNING"]
    assert page["meta"]["matched"] == 2
    assert page["meta"]["exists"] is True
    assert page["meta"]["sources"][0]["name"] == "app"


def test_read_logs_keyword_filter(log_file):
    page = logs_module.read_logs(source="app", keyword="commander", limit=50)
    assert len(page["items"]) == 1
    assert page["items"][0]["logger"] == "app.modules.commander.service"


def test_read_logs_redacts_secrets(log_file):
    page = logs_module.read_logs(source="app", level="ERROR", limit=50)
    message = page["items"][0]["message"]
    assert "sk-secret123456" not in message
    assert "abcdefgh12345678" not in message
    assert "***" in message


def test_read_logs_merges_continuation_lines(log_file):
    page = logs_module.read_logs(source="app", keyword="Traceback", limit=50)
    assert len(page["items"]) == 1
    assert "Traceback" in page["items"][0]["message"]


def test_export_text_redacts(tmp_path, monkeypatch):
    path = tmp_path / "app.log"
    path.write_text(
        "2026-11-01 10:00:00,000 ERROR [x] [r] token=abc123secret\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(logs_module, "log_paths", lambda: {"app": path, "engine": path})
    filename, text = logs_module.export_text(source="app")
    assert filename.startswith("app-logs-")
    assert "abc123secret" not in text
    assert "token=***" in text


def test_redact_github_tokens():
    assert logs_module.redact("ghp_abcdefghijklmnopqrstuvwxyz") == "ghp_***"
