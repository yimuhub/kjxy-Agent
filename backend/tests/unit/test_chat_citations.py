from app.modules.chat.models import ChatMessage
from app.modules.chat.service import _normalize_citations, citations_from_result, message_out


def test_normalize_filters_unknown_and_maps_snippet():
    out = _normalize_citations(
        [
            {
                "title": "水稻分蘖期",
                "snippet": "浅水勤灌",
                "url": "https://example.com/a",
                "secret": "drop",
            }
        ]
    )
    assert out == [
        {
            "title": "水稻分蘖期",
            "snippet": "浅水勤灌",
            "url": "https://example.com/a",
            "quote": "浅水勤灌",
            "source": "https://example.com/a",
        }
    ]


def test_normalize_caps_count_and_length():
    out = _normalize_citations([{"title": "x" * 900} for _ in range(30)])
    assert len(out) == 20
    assert len(out[0]["title"]) == 600


def test_citations_from_agent_raw():
    result = {
        "raw": {
            "citations": [{"document_id": "d1", "chunk_id": "c1", "title": "资料", "quote": "原文"}]
        }
    }
    assert citations_from_result(result)[0]["document_id"] == "d1"


def test_citations_from_team_research_merges_kinds():
    result = {
        "research": {
            "knowledge": [{"title": "知识", "quote": "片段"}],
            "web": [{"title": "网页", "url": "https://example.com", "snippet": "摘要"}],
        }
    }
    out = citations_from_result(result)
    assert [c["kind"] for c in out] == ["knowledge", "web"]
    assert out[1]["quote"] == "摘要"


def test_message_out_exposes_citations():
    msg = ChatMessage(
        session_id="s1",
        yard_id="y1",
        seq=1,
        role="assistant",
        content="答案",
        citations_json=[{"title": "资料", "quote": "原文"}],
    )
    assert message_out(msg)["citations"] == [{"title": "资料", "quote": "原文"}]
