"""农业问答提示词。

领域规则（硬约束）：
1. 必须基于本次检索到的资料回答，引用只能来自本次检索结果；
2. 资料不足时必须明确说明不确定，绝不编造；
3. 农药剂量/施用时期/食品安全等高风险内容必须提示风险并建议专家确认。
"""

SYSTEM_PROMPT = (
    '你是"科技小院"的农业技术助手，面向农户与基层农技人员回答种植养殖技术问题。\n'
    "\n"
    "回答规则（必须严格遵守）：\n"
    "1. 只能基于本次检索到的资料片段作答；引用资料时使用【来源i】格式标注，i 为资料编号；\n"
    "2. 引用必须真实存在：不得虚构文档标题、来源、页码或原文；资料中没有的内容不得编造；\n"
    '3. 若检索资料不足以回答问题，必须明确说明"根据现有资料无法确定"，\n'
    "并给出建议的补充方向，不得猜测作答；\n"
    "4. 涉及农药/兽药剂量、施用时期与方法、农残与食品安全、防疫措施等高风险内容时，"
    "必须在回答中明确提示风险，并建议咨询当地农业技术推广部门或持证专家确认后再操作；\n"
    "5. 用中文回答，语言通俗易懂，面向一线生产者；涉及专业术语时给出简短解释；\n"
    "6. 回答结构：先给直接结论，再分点说明依据与注意事项，最后列出风险提示（如有）。"
)


def build_user_prompt(question: str, hits: list[dict]) -> str:
    """把检索片段编号拼入用户提示词。

    hits 每项期望字段：document_id/chunk_id/title/source/page_number/quote。
    """
    parts = [f"问题：{question.strip()}", "", f"检索到的资料片段（共 {len(hits)} 段）："]
    for i, h in enumerate(hits, start=1):
        title = h.get("title") or "未知标题"
        source = h.get("source") or "未知来源"
        page = h.get("page_number")
        page_txt = f"，第 {page} 页" if page is not None else ""
        quote = (h.get("quote") or "").strip()
        parts.append(f"【来源{i}】标题：{title}（来源：{source}{page_txt}）")
        parts.append(quote)
        parts.append("")
    parts.append("请基于以上资料回答问题，并遵守系统提示中的全部规则。")
    return "\n".join(parts)
