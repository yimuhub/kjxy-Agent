"""文档生成模块：统一的文档模型（DocModel）与多格式渲染器。

设计口径：
- **一个模型，多份产物**：Markdown / Word / Excel / PPT / PDF 都从同一 DocModel 渲染，
  预览 HTML 也从模型直接渲染——预览与文件天然一致，无需解析 Office 文件；
- **商用许可安全**：仅使用 MIT/BSD 依赖（python-docx / openpyxl / python-pptx /
  reportlab）；不引入 GPL/AGPL 组件（pandoc、LibreOffice、PyMuPDF 等一律不用）；
- **护栏前移**：块数/文本/表格行列均有限制，超限 400，避免一次生成打爆内存与成本。
"""
