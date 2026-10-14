"""The documents attached to an email: PDFs as they are, pictures as JPEGs the right way up.

What the sender meant as an attachment but cannot be used is reported with a reason, so the app
can tell them ("I could not read invoice.docx: only PDFs and pictures are read") instead of a
vague "nothing readable was found".
"""

import io
from dataclasses import dataclass
from email.message import EmailMessage
from enum import Enum

from PIL import Image, ImageOps

_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif")
_HARMLESS_EXT = (".ics", ".vcf")  # calendar invites and contact cards: read elsewhere or not worth a word


class SkipReason(str, Enum):
    TOO_BIG = "too_big"
    TOO_MANY = "too_many"
    UNREADABLE = "unreadable"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class Limits:
    # Smaller pictures are logos and signature icons, not documents: they are ignored in silence
    min_image_bytes: int = 30 * 1024
    max_files: int = 5
    max_pdf_bytes: int = 10 * 1024 * 1024
    max_image_side: int = 2000  # pictures are scaled down to this, then saved as JPEG
    jpeg_quality: int = 88


DEFAULT_LIMITS = Limits()


@dataclass
class Attachment:
    filename: str
    media_type: str  # "application/pdf" or "image/jpeg"
    data: bytes


@dataclass
class SkippedFile:
    filename: str
    reason: SkipReason


def normalise_image(raw: bytes, limits: Limits = DEFAULT_LIMITS) -> bytes:
    """Any picture as a JPEG of a reasonable size, the right way up."""
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)  # phone photos come rotated
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    if max(img.size) > limits.max_image_side:
        img.thumbnail((limits.max_image_side, limits.max_image_side), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=limits.jpeg_quality)
    return out.getvalue()


def message_files(msg: EmailMessage, limits: Limits = DEFAULT_LIMITS) -> tuple[list[Attachment], list[SkippedFile]]:
    """(attachments, skipped) of a MIME message."""
    files, skipped = [], []
    for part in msg.walk():
        if part.is_multipart():
            continue
        kind = part.get_content_type().lower()
        name = part.get_filename() or ""
        low = name.lower()
        attached = part.get_content_disposition() == "attachment"
        pdf = kind == "application/pdf" or low.endswith(".pdf")
        image = kind.startswith("image/") or low.endswith(_IMAGE_EXT)
        if not pdf and not image:
            harmless = kind.startswith("text/") or kind == "application/ics" or low.endswith(_HARMLESS_EXT)
            if attached and not harmless:
                skipped.append(SkippedFile(name or kind, SkipReason.UNSUPPORTED))
            continue
        try:
            raw = part.get_payload(decode=True) or b""
        except Exception:
            raw = b""
        if image and len(raw) < limits.min_image_bytes:
            continue
        label = name or ("document.pdf" if pdf else "image")
        if len(files) >= limits.max_files:
            skipped.append(SkippedFile(label, SkipReason.TOO_MANY))
        elif pdf and len(raw) > limits.max_pdf_bytes:
            skipped.append(SkippedFile(label, SkipReason.TOO_BIG))
        elif pdf and not raw.startswith(b"%PDF"):
            skipped.append(SkippedFile(label, SkipReason.UNREADABLE))
        elif pdf:
            files.append(Attachment(label, "application/pdf", raw))
        else:
            try:
                files.append(Attachment(label, "image/jpeg", normalise_image(raw, limits)))
            except Exception:  # not really a picture, or one too big to open safely
                skipped.append(SkippedFile(label, SkipReason.UNREADABLE))
    return files, skipped
