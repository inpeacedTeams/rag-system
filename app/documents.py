import io
import re
from dataclasses import dataclass
from pypdf import PdfReader


class DocumentError(ValueError):
    pass


@dataclass(frozen=True)
class Chunk:
    text: str
    page: int | None
    number: int


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\x00", " ")).strip()


def chunk_pages(pages: list[tuple[int | None, str]], size: int = 1400, overlap: int = 220) -> list[Chunk]:
    if size <= overlap or overlap < 0:
        raise ValueError("Invalid chunk settings")
    chunks = []
    for page, raw in pages:
        text = normalize(raw)
        start = 0
        while start < len(text):
            end = min(start + size, len(text))
            if end < len(text):

                boundary = max(text.rfind(". ", start + size // 2, end), text.rfind(" ", start + size // 2, end))
                if boundary > start:
                    end = boundary + 1
            fragment = text[start:end].strip()
            if fragment:
                chunks.append(Chunk(fragment, page, len(chunks) + 1))
            if end == len(text):
                break
            start = max(start + 1, end - overlap)
    if not chunks:
        raise DocumentError("В документе нет извлекаемого текста. Для сканированного PDF сначала выполните OCR.")
    return chunks


def extract_file(name: str, data: bytes, max_pages: int = 300) -> list[Chunk]:
    suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if suffix == "txt":
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise DocumentError("TXT должен быть в кодировке UTF-8.") from exc
        return chunk_pages([(None, text)])
    if suffix != "pdf":
        raise DocumentError("Поддерживаются только PDF и TXT.")
    if not data.startswith(b"%PDF-"):
        raise DocumentError("Файл не является PDF.")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise DocumentError("Зашифрованные PDF не поддерживаются. Загрузите файл без пароля.")
        if len(reader.pages) > max_pages:
            raise DocumentError(f"В PDF должно быть не больше {max_pages} страниц.")
        return chunk_pages([(i + 1, page.extract_text() or "") for i, page in enumerate(reader.pages)])
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError("Не удалось прочитать PDF. Проверьте, что файл не повреждён.") from exc
