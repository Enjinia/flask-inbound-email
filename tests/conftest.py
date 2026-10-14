"""Made-up emails for the tests: every name and address here is invented."""

import io
import os
from email.message import EmailMessage

import pytest
from PIL import Image


def make_email(text="", *, html=None, sender="Alice Example <alice@example.com>", subject="Hello", headers=None,
               attachments=(), message_id="<abc123@example.com>"):
    """attachments: (filename, media_type, data, inline) tuples."""
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "alice-x7k2m9p4q1@in.example.org"
    msg["Subject"] = subject
    if message_id:
        msg["Message-ID"] = message_id
    for name, value in (headers or {}).items():
        msg[name] = value
    msg.set_content(text)
    if html is not None:
        msg.add_alternative(html, subtype="html")
    for filename, media_type, data, inline in attachments:
        maintype, subtype = media_type.split("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename,
                           disposition="inline" if inline else "attachment")
    return msg


def picture(width=800, height=600, fmt="PNG", exif_orientation=None):
    """A noisy picture, big enough not to be taken for a signature icon."""
    img = Image.frombytes("RGB", (width, height), os.urandom(width * height * 3))
    out = io.BytesIO()
    if exif_orientation:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation
        img.save(out, format="JPEG", exif=exif)
    else:
        img.save(out, format=fmt)
    return out.getvalue()


@pytest.fixture
def pdf():
    return b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"
