"""Парсинг файлов промта: .txt / .md / .pdf.

Telegram Bot API поддерживает скачивание файлов до 20MB.
LLM (MiniMax M3) держит ~200K input tokens = ~150K русских / 300K английских символов.

Лимит в коде: 100K символов (защита от памяти).
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

from aiogram import Bot
from aiogram.types import Document

logger = logging.getLogger(__name__)

# Лимиты
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB (Bot API hard limit)
MAX_TEXT_CHARS = 100_000  # защита от переполнения памяти

# Поддерживаемые MIME типы
SUPPORTED_MIME = {
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/x-markdown": ".md",
    "application/pdf": ".pdf",
    # Telegram не всегда ставит правильный MIME для .md
}


async def download_and_extract(bot: Bot, document: Document) -> tuple[str, str]:
    """Скачать документ и извлечь текст.

    Returns:
        (text, filename) — содержимое и имя файла

    Raises:
        ValueError: если файл слишком большой или неподдерживаемый формат
    """
    # Проверка размера
    if document.file_size and document.file_size > MAX_FILE_SIZE:
        raise ValueError(
            f"Файл слишком большой: {document.file_size // 1024 // 1024}MB. "
            f"Максимум: {MAX_FILE_SIZE // 1024 // 1024}MB"
        )

    # Скачиваем
    file = await bot.get_file(document.file_id)
    file_bytes_io = io.BytesIO()
    await bot.download_file(file.file_path or "", destination=file_bytes_io)
    file_bytes = file_bytes_io.getvalue()

    filename = document.file_name or "file"
    file_ext = Path(filename).suffix.lower()

    # Текстовые форматы
    if file_ext in (".txt", ".md", ".markdown"):
        try:
            text = file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            text = file_bytes.decode("latin-1", errors="ignore")
        return _truncate(text, filename)

    # PDF
    if file_ext == ".pdf" or document.mime_type == "application/pdf":
        text = _extract_pdf(file_bytes)
        return _truncate(text, filename)

    # Неподдерживаемый формат
    raise ValueError(
        f"Неподдерживаемый формат: {file_ext}. " f"Поддерживаются: .txt, .md, .pdf"
    )


def _extract_pdf(file_bytes: bytes) -> str:
    """Извлечь текст из PDF через pdfplumber."""
    try:
        import pdfplumber  # type: ignore[import-untyped]
    except ImportError as e:
        raise ValueError("PDF-парсинг недоступен: pdfplumber не установлен") from e

    text_parts: list[str] = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            page_text = page.extract_text() or ""
            if page_text.strip():
                text_parts.append(f"--- Страница {page_num} ---\n{page_text}")
    return "\n\n".join(text_parts)


def _truncate(text: str, filename: str) -> tuple[str, str]:
    """Обрезать текст до лимита с предупреждением."""
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]
        text += (
            f"\n\n... [обрезано: файл содержал >{MAX_TEXT_CHARS:,} символов, "
            f"показаны первые {MAX_TEXT_CHARS:,}]"
        )
        logger.warning(
            "Файл %s обрезан до %d символов",
            filename,
            MAX_TEXT_CHARS,
        )
    return text, filename
