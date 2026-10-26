"""DocModel → 预览 HTML（自包含、全量转义、无脚本）。

预览与导出文件同源：都从 DocModel 渲染，因此「预览看到的就是下载到的」。
HTML 里不引外部资源、不执行脚本，配合响应头 CSP sandbox 使用。
"""

from __future__ import annotations

from html import escape

from app.modules.docs.model import DocBlock, DocModel

_STYLE = """
:root{color-scheme:dark}
html,body{margin:0;padding:0;background:#0B0E14;color:#E8EAF2;
  font:15px/1.75 -apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif}
.doc{max-width:820px;margin:0 auto;padding:36px 28px 64px}
h1.doc-title{font-size:26px;line-height:1.35;margin:0 0 8px}
.doc-meta{color:#7C8498;font-size:13px;margin:0 0 4px}
h2{font-size:19px;margin:26px 0 8px}
h3{font-size:16.5px;margin:22px 0 6px}
h4{font-size:15px;margin:18px 0 6px}
p{margin:8px 0;white-space:pre-wrap}
ul,ol{margin:8px 0;padding-left:24px}
li{margin:4px 0}
pre{background:#171C2A;border:1px solid rgba(255,255,255,.09);border-radius:10px;
  padding:12px 14px;overflow:auto;white-space:pre-wrap;font-size:13px}
table{border-collapse:collapse;width:100%;margin:12px 0}
th,td{border:1px solid rgba(255,255,255,.14);padding:7px 10px;text-align:left;vertical-align:top}
th{background:rgba(99,102,241,.16);font-weight:600}
hr.pb{border:none;border-top:1px dashed rgba(255,255,255,.18);margin:28px 0}
""".strip()


def _render_block(block: DocBlock) -> str:
    if block.type == "heading":
        level = min(block.level + 1, 4)
        return f"<h{level}>{escape(block.text)}</h{level}>"
    if block.type == "paragraph":
        return f"<p>{escape(block.text)}</p>"
    if block.type == "bullets":
        items = "".join(f"<li>{escape(i)}</li>" for i in block.items)
        return f"<ul>{items}</ul>"
    if block.type == "numbered":
        items = "".join(f"<li>{escape(i)}</li>" for i in block.items)
        return f"<ol>{items}</ol>"
    if block.type == "code":
        return f"<pre>{escape(block.text)}</pre>"
    if block.type == "table":
        head = "".join(f"<th>{escape(c)}</th>" for c in block.header)
        body = "".join(
            "<tr>" + "".join(f"<td>{escape(c)}</td>" for c in row) + "</tr>" for row in block.rows
        )
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    return '<hr class="pb"/>'


def to_preview_html(model: DocModel) -> str:
    meta = "".join(
        f'<p class="doc-meta">{escape(value)}</p>'
        for value in (model.subject, model.author)
        if value
    )
    blocks = "".join(_render_block(b) for b in model.blocks)
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"/>'
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; style-src 'unsafe-inline'\"/>"
        f'<meta name="viewport" content="width=device-width, initial-scale=1"/>'
        f"<title>{escape(model.title)}</title><style>{_STYLE}</style></head>"
        f'<body><main class="doc"><h1 class="doc-title">{escape(model.title)}</h1>'
        f"{meta}{blocks}</main></body></html>"
    )
