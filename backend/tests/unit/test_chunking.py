"""切块单元测试：顺序、尺寸、超长段落硬切、重叠窗口。"""

from app.modules.knowledge.chunking import split_text


def test_empty_text_returns_empty():
    assert split_text("") == []
    assert split_text("   \n  ") == []


def test_order_preserved():
    text = "第一段内容。\n\n第二段内容。\n\n第三段内容。"
    chunks = split_text(text, chunk_size=12, overlap=4)
    assert len(chunks) >= 2
    full = "\n\n".join(chunks)
    positions = [full.index("第一段内容"), full.index("第二段内容"), full.index("第三段内容")]
    assert positions == sorted(positions)


def test_chunk_size_respected():
    text = "\n\n".join(f"段落{i}：" + "水稻分蘖期田间管理要点。" * 20 for i in range(5))
    chunks = split_text(text, chunk_size=100, overlap=10)
    assert len(chunks) > 1
    # 首块不超过 chunk_size；后续块因重叠窗口可超出，但不超过 chunk_size + overlap
    assert len(chunks[0]) <= 100
    for c in chunks[1:]:
        # 重叠窗口会让后续块超出 chunk_size：最多多出 overlap + 连接符 "\n\n"
        assert len(c) <= 100 + 10 + 2


def test_long_paragraph_hard_cut():
    para = "x" * 1000
    chunks = split_text(para, chunk_size=100, overlap=10)
    assert len(chunks) >= 9
    # 硬切后内容无丢失（忽略重叠窗口的重复拼接）
    assert chunks[0].startswith("x" * 100)
    assert all(set(c.replace("\n", "")) <= {"x"} for c in chunks)


def test_overlap_window():
    text = "a" * 60 + "\n\n" + "b" * 60
    chunks = split_text(text, chunk_size=70, overlap=20)
    assert len(chunks) == 2
    # 第二块头部带有第一块尾部的重叠窗口
    assert chunks[1].startswith(chunks[0][-20:])


def test_no_overlap_when_zero():
    text = "a" * 60 + "\n\n" + "b" * 60
    chunks = split_text(text, chunk_size=70, overlap=0)
    assert len(chunks) == 2
    assert chunks[1] == "b" * 60


def test_invalid_chunk_size():
    import pytest

    with pytest.raises(ValueError):
        split_text("abc", chunk_size=0)
