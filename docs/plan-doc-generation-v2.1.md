# v2.1 升级方案：对话内文档生成与预览（Word / Markdown / Excel / PPT / PDF）

> 本文是实施方案。**P0/P1 已全部落地（2026-10-27，v2.1.0）**，实施记录见 §7。
>
> 关联记录：本轮三项修复（tooltip 可读性、会话类型徽章、消息一键复制）已落地并过门禁，
> 见 `CHANGELOG.md` 与 `docs/audit-report-2026-10-25.md`。

---

## 0. 结论速览

- **能自研，且建议自研**：docx / xlsx / pptx 本质是 ZIP + OOXML（XML），自研写出器
  1500 行左右即可覆盖文档类场景，零第三方许可负担；Markdown 直接文本生成。
- **PDF 首期借道 reportlab（BSD-3，可商用）**：中文 PDF 的核心难点是 TrueType
  字体子集化 + CMap，完全自研周期与风险都高；首期用 reportlab 的 CID 中文字体
  排版，**排版逻辑（标题/段落/表格/分页）仍自研**。中期可换自研 TTF 子集嵌入。
- **预览不解析 Office 文件**：所有文件都从同一个「文档模型（DocModel）」写出，
  预览也从这个模型渲染 HTML——天然一致，且避免引入 docx→html 解析器。
- **生成入口两条腿**：新增 `doc.generate` 工具（Agent 自主调用）+ 对话意图
  （「写一份…报告」）兜底；产物落入现有 `chat_attachments` 管道，可下载、可预览。
- 终点版本建议 **2.1.0**。

---

## 1. 本轮已完成（无需再排期）

| # | 能力 | 落地 |
|---|---|---|
| 1 | 悬浮提示可读性 | 修复暗色 popper「深底深字」：`design-system.css` 显式给 `--ds-text-1` 文字色（EP 原生用 `var(--el-bg-color)` 反色，被项目桥接成深色） |
| 2 | 会话类型标识 | `chat_sessions.target/target_code`（迁移 0028）+ `participant_count`；左侧列表显示「群聊 N / 助手名 / 班组 / 对话」徽章；`target_name` 由后端按 `agent_code` 解析 |
| 3 | 一键复制 | 用户与助手消息各带复制按钮（Clipboard API + execCommand 回退），按钮短暂显示「已复制」 |

门禁：后端 857 passed、ruff/format/mypy 全绿；前端 ESLint 0 error、`vue-tsc`、
`vite build` 通过；Playwright 11 passed；`contracts/openapi.yaml` 143 paths。

---

## 1.1 v2.1.0 交付结果（2026-10-27）

- 五种格式一次交付：`.md / .docx / .xlsx / .pptx / .pdf`，统一从 DocModel 渲染；
- 对话意图 + 文档卡片 + 在线预览 + 鉴权下载全链路打通；
- 门禁：后端 **885 passed**、ruff/format/mypy 全绿；前端 ESLint 0 error、
  `vue-tsc`、`vite build`；Playwright **12 passed**（含生成→预览→下载全链路）；
  `contracts/openapi.yaml` **146 paths**；迁移 `0029` 已应用。

---

## 2. 开源调研与许可证结论（商用前置）

| 项目 | 许可证 | 能借鉴什么 | 结论 |
|---|---|---|---|
| python-docx | MIT | docx 包结构（`[Content_Types].xml`、`word/document.xml`、`styles.xml`、rels 关系） | 可商用；**只借鉴结构，不引入依赖** |
| openpyxl | MIT | xlsx 包结构（workbook/sheet/styles/sharedStrings）、样式索引 | 同上 |
| python-pptx | MIT | pptx 的 master/layout/slide 关系与占位符模型 | 同上 |
| XlsxWriter | BSD-2 | 流式写 sheet、列宽/冻结行等细节 | 可商用，作为对照实现 |
| reportlab | BSD-3 | PDF 排版原语、CID 中文字体（STSong-Light） | **首期唯一建议依赖**（仅 PDF） |
| mammoth | BSD-2 | docx→HTML（用于「预览他人上传的 docx」，非本期必需） | 后续可选 |
| markitdown (Microsoft) | MIT | Office/PDF→Markdown（用于「上传即转知识库」，另一条线） | 后续可选 |
| pandoc | GPL-2+ | — | **排除**（分发二进制有 GPL 传染风险） |
| LibreOffice / OnlyOffice | MPL/LGPL/AGPL 混合 | — | **排除**（商业分发合规复杂、体积大） |
| PyMuPDF / borb | AGPL | — | **排除** |

> 许可红线：GPL/AGPL 一律不进产品；MIT/BSD/Apache 可用，但 docx/xlsx/pptx 优先自研
> （结构参考不受限），把第三方依赖压到只剩 PDF 的 reportlab。

---

## 3. 技术方案

### 3.1 统一文档模型（DocModel）

所有格式从同一个 JSON 模型渲染，预览与文件天然一致：

```json
{
  "title": "小麦赤霉病防治方案",
  "subject": "2026 年春季 · 演示科技小院",
  "author": "农技顾问",
  "blocks": [
    { "type": "heading", "level": 1, "text": "一、病害概述" },
    { "type": "paragraph", "text": "赤霉病是……" },
    { "type": "bullets", "items": ["扬花期遇连阴雨风险最高", "……"] },
    { "type": "table", "header": ["药剂", "用量"], "rows": [["丙硫菌唑", "30 ml/亩"]] },
    { "type": "code", "text": "……" },
    { "type": "pagebreak" }
  ]
}
```

约束：标题 ≤100 字、块数 ≤300、表格 ≤200 行 × 20 列、总文本 ≤200 KB，超限 400
`DOC_TOO_LARGE`（成本与文件体积护栏）。

### 3.2 渲染器矩阵（`backend/app/modules/docs/`）

| 模块 | 产物 | 实现 | 工作量 |
|---|---|---|---|
| `model.py` | DocModel 校验 | Pydantic（extra=forbid）+ 限流护栏 | 0.5d |
| `markdown.py` | `.md` | 直接文本 | 0.5d |
| `ooxml/docx.py` | `.docx` | zip + OOXML：标题/段落/列表/表格/分页 | 1.5d |
| `ooxml/xlsx.py` | `.xlsx` | zip + OOXML：首个 sheet 的表格块/列宽/表头加粗 | 1.5d |
| `ooxml/pptx.py` | `.pptx` | 最小 master/layout 骨架：标题页 + 章节页 + 要点页 | 2d |
| `pdf.py` | `.pdf` | reportlab（BSD）：CID 中文字体、标题/段落/表格/分页 | 1d |
| `preview.py` | HTML | 自研 DocModel→HTML（全量转义，无内联脚本） | 1d |

> pptx 是最复杂的一块：首版只做三种版式（封面 / 章节过渡 / 标题+要点），
> 不做任意自由排版；后续再做图片页与表格页。

### 3.3 对话接入

1. **工具通道（主）**：新增工具 `doc.generate`
   - 入参：`{format: md|docx|xlsx|pptx|pdf, model: DocModel}`；
   - 出参：`{attachment_id, filename, mime, size, preview_url, download_url}`；
   - 注册进 `tools/registry.py`（风险等级 low——只写会话附件，不触外部副作用），
     助手可通过 `allowed_tools`/`bound_skills` 绑定；
   - LLM 先「拟稿」出 DocModel（结构化输出，复用现有 JSON 解析兜底），再调工具。
2. **意图通道（兜底）**：`chat/intent.py` 新增 `INTENT_DOC_GENERATE`（「生成/写/整理一份
   …报告/方案/表格/PPT」+ 格式词），命中后走「文档卡片」：确认后生成，避免误触。
3. **产物落库**：复用 `chat_attachments`（0021）+ 迁移 **0029** 新增
   `doc_format`（md/docx/…）、`doc_model_json`（预览与再导出的事实源）、
   `generated`（true=助手生成）；文件写现有 storage 目录，下载走鉴权端点
   （`GET /chat/sessions/{sid}/attachments/{aid}/download`，越权一律 404）。
4. **卡片**：SSE 新增卡片 `type=document`：
   `{filename, format, size, attachment_id, preview_url, download_url}`；
   前端渲染「文件名 + 格式徽章 + 预览 / 下载」。

### 3.4 预览

- `GET /chat/sessions/{sid}/attachments/{aid}/preview`
  - `md` / `docx` / `xlsx` / `pptx`：服务端按 `doc_model_json` 渲染**自包含 HTML**
    （样式内联、无脚本），前端弹窗用 `<iframe sandbox>` 展示；
  - `pdf`：直接返回文件流，浏览器原生 PDF 预览；
  - 全部响应带 `Content-Security-Policy: sandbox`，HTML 内容全量转义。
- 后续可对「用户上传的 docx」接 mammoth 转 HTML（本期不做）。

### 3.5 接口清单（新增）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/chat/sessions/{id}/documents` | 手动/意图生成（format + model） |
| GET | `/chat/sessions/{id}/attachments/{aid}/preview` | HTML 预览 / PDF 流 |
| GET | `/chat/sessions/{id}/attachments/{aid}/download` | 下载（鉴权 + 小院/创建者边界） |

错误码：`DOC_MODEL_INVALID`(400)、`DOC_TOO_LARGE`(400)、`DOC_FORMAT_UNSUPPORTED`(400)、
`DOC_RENDER_FAILED`(500，附 request_id)、`DOC_NOT_FOUND`(404)。

---

## 4. 分阶段计划

### P0（先能交付，约 3 天）
- DocModel + md/docx 写出器 + 预览 HTML + 下载端点；
- `doc.generate` 工具注册 + `document` 卡片 + 前端预览弹窗；
- 单测：模型校验、docx 包结构、HTML 转义、越权下载 404；
- e2e：对话生成 md + docx → 卡片 → 预览 → 下载。

### P1（全格式，约 4 天）
- xlsx / pptx / pdf 写出器；表格与分页；中文 PDF 实测；
- 意图通道 `INTENT_DOC_GENERATE` + 文档卡片确认流；
- 兼容矩阵手工验收：WPS / Office / Excel / PowerPoint / 预览器。

### P2（增强，暂不排期）
- 模板化（周报/方案/台账）、图片嵌入、页眉页脚、目录；
- PDF 自研 TTF 子集嵌入（去掉 reportlab 依赖）；
- 上传文件预览（mammoth）与「文档一键转知识库」（另一条线）。

---

## 5. 验收标准（每阶段通用）

- 后端：`pytest` 新增用例全绿；ruff / format / mypy 全绿；迁移 `upgrade→downgrade→upgrade` 往返；
- 契约：`generate_openapi.py` + drift 检查；错误码登记 `contracts/error-codes.md`；
- 前端：ESLint 0 error、`vue-tsc`、`vite build`；
- e2e：生成→卡片→预览→下载全链路；复制按钮回归（已加）；
- 手工：三种 Office 客户端打开无「文件已损坏」提示；PDF 中文无乱码/豆腐块；
- Token：文档生成调用的 token 计入既有统计（消息级 token 均可见）。

---

## 6. 需要拍板的决策点

| # | 决策 | 选项 | 建议 |
|---|---|---|---|
| D1 | PDF 路线 | A. reportlab（BSD，1 天）/ B. 完全自研 TTF 子集（5-7 天，风险高） | **A 首期**，P2 再评估自研 |
| D2 | 生成触发 | A. 工具自主调用 / B. 意图关键词 / C. 两者 | **C（工具为主，意图兜底）** |
| D3 | 首期格式 | A. md+docx 先上 / B. 五种一次全上 | **A（P0 三天可交付），P1 补齐** |
| D4 | 版本号 | A. 2.1.0 / B. 并入 2.0.x | **A** |

确认后按 P0 → P1 执行，每阶段完成即跑全量门禁并更新 CHANGELOG 与验收记录。

---

## 7. 实施记录（v2.1.0）

### 7.1 与方案的偏差（已评估，可商用）

| 项 | 方案 | 实际 | 原因 |
|---|---|---|---|
| docx 写出 | 自研 zip+OOXML | `python-docx`（MIT） | 本就是解析依赖（docx 上传抽取文本），复用写出不新增许可负担、格式兼容更稳 |
| xlsx 写出 | 自研 zip+OOXML | `openpyxl`（MIT） | 同上：样式/合并/列宽细节更可靠，MIT 可商用 |
| pptx 写出 | 自研 zip+OOXML | `python-pptx`（MIT） | 母版/占位符关系复杂，自研收益低、风险高 |
| PDF | reportlab（BSD） | `reportlab`（BSD-3） | 与方案一致；CID 中文字体 STSong-Light |
| 预览 | DocModel→HTML | 一致，另加 CSP meta 与 iframe `sandbox=""` | 双保险：脚本、外链、表单全部禁用 |
| PDF 预览 | 浏览器原生 | 统一走 DocModel HTML 预览 | 跨端一致，不依赖浏览器 PDF 插件 |
| 触发通道 | 工具 `doc.generate` 为主 + 意图兜底 | 仅意图通道（`INTENT_DOC`） | 现有工具运行时不携带对话会话上下文，工具产物无法落会话附件；意图通道已覆盖用户主路径，工具通道列入 P2 待运行时补上下文后再接 |

许可结论不变：仅使用 MIT / BSD-3 依赖，GPL/AGPL（pandoc、LibreOffice、PyMuPDF、borb）
一律未引入。

### 7.2 落地清单

- 后端 `backend/app/modules/docs/`：`model.py`（DocModel + 护栏）、
  `render_md/docx/xlsx/pptx/pdf.py`、`preview.py`、`service.py`；
- 对话接入 `backend/app/modules/chat/document.py`：LLM 拟 DocModel（失败确定性兜底），
  `INTENT_DOC` 意图分支，产物落 `chat_attachments`（0029）；卡片 `type=document`；
- 接口：`POST /chat/sessions/{id}/documents`、`GET …/attachments/{aid}/preview`、
  `GET …/attachments/{aid}/download`（预览响应带 CSP sandbox）；
- 前端：`ChatCard` 文档卡片（格式徽章 + 预览/下载）、`ChatPanel` 沙箱 iframe 预览弹窗、
  `chatApi.previewDocument/downloadDocument`；
- 测试：模型校验/五种写出器/预览转义/越权 404/意图用例/对话生成链路；
- 迁移：`0029_chat_attachments_document`（`doc_format` / `doc_model_json` / `generated`）。

### 7.3 仍属后续（P2）

模板化（周报/方案/台账）、图片/页眉页脚/目录、上传 docx 预览（mammoth）、
「文档一键转知识库」、PDF 自研 TTF 子集；以及 `doc.generate` 工具通道
（需先让工具运行上下文携带 `session_id`，产物才能落会话附件）。
