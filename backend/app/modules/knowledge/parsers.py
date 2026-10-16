"""文档文本提取：txt/md/csv 直接读；pdf 按页提取；docx 按段落合并为单页。

返回 (full_text, page_count, pages)。未知类型抛 UNSUPPORTED_FILE_TYPE(415)。
扫描型/无文字 PDF 返回空文本（page_count 保留），由调用方决定如何处理。
"""

from io import BytesIO

from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.knowledge.storage import LocalStorageBackend

log = get_logger(__name__)

PDF_MIMES = {"application/pdf"}
DOCX_MIMES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
TEXT_MIMES = {
    "text/plain",
    "text/markdown",
    "text/csv",
    "text/x-csv",
    "application/csv",
}


def _get_backend() -> LocalStorageBackend:
    return LocalStorageBackend()


def _read_text_bytes(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def _extract_pdf(data: bytes) -> tuple[str, int | None, list[str]]:
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data))
    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    page_count: int | None = len(pages)
    full_text = "\n\n".join(pages).strip()
    if not full_text:
        log.info("pdf 提取结果为空，可能是扫描型 PDF")
    return full_text, page_count, pages


def _extract_docx(data: bytes) -> tuple[str, int | None, list[str]]:
    from docx import Document

    doc = Document(BytesIO(data))
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
    full_text = "\n\n".join(paragraphs)
    # docx 按段落合并为单页
    return full_text, None, [full_text]


def extract_text(storage_key: str, mime: str) -> tuple[str, int | None, list[str]]:
    data = _get_backend().open(storage_key).read()
    mime = (mime or "").split(";")[0].strip().lower()

    if mime in PDF_MIMES:
        return _extract_pdf(data)
    if mime in DOCX_MIMES:
        return _extract_docx(data)
    if mime in TEXT_MIMES or mime.startswith("text/"):
        full_text = _read_text_bytes(data).strip()
        return full_text, None, [full_text]
    raise AppError(
        "UNSUPPORTED_FILE_TYPE", f"不支持的文件类型：{mime or '未知'}。", http_status=415
    )
