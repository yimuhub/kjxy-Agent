"""DocModel → Markdown（纯文本渲染，无第三方依赖）。"""

from __future__ import annotations

from app.modules.docs.model import DocBlock, DocModel


def _table_md(block: DocBlock) -> list[str]:
    header = "| " + " | ".join(c.replace("|", "\\|") for c in block.header) + " |"
    sep = "| " + " | ".join("---" for _ in block.header) + " |"
    rows = ["| " + " | ".join(c.replace("|", "\\|") for c in row) + " |" for row in block.rows]
    return [header, sep, *rows]


def to_markdown(model: DocModel) -> bytes:
    lines: list[str] = [f"# {model.title}", ""]
    if model.subject:
        lines += [f"> {model.subject}", ""]
    if model.author:
        lines += [f"作者：{model.author}", ""]
    for block in model.blocks:
        if block.type == "heading":
            lines += ["#" * (block.level + 1) + f" {block.text}", ""]
        elif block.type == "paragraph":
            lines += [block.text, ""]
        elif block.type == "bullets":
            lines += [f"- {item}" for item in block.items] + [""]
        elif block.type == "numbered":
            lines += [f"{i}. {item}" for i, item in enumerate(block.items, start=1)] + [""]
        elif block.type == "code":
            lines += ["```", block.text, "```", ""]
        elif block.type == "table":
            lines += _table_md(block) + [""]
        elif block.type == "pagebreak":
            lines += ["---", ""]
    while lines and lines[-1] == "":
        lines.pop()
    return ("\n".join(lines) + "\n").encode("utf-8")
