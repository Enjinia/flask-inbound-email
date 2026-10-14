import io

from conftest import make_email, picture
from PIL import Image

from flask_inbound_email import Limits, SkipReason
from flask_inbound_email.attachments import message_files


def test_pdf_is_kept_as_it_is(pdf):
    msg = make_email("see attached", attachments=[("invoice.pdf", "application/pdf", pdf, False)])
    files, skipped = message_files(msg)
    assert [(f.filename, f.media_type, f.data) for f in files] == [("invoice.pdf", "application/pdf", pdf)]
    assert skipped == []


def test_pictures_become_jpeg_scaled_down():
    msg = make_email("photo", attachments=[("receipt.png", "image/png", picture(3000, 1000), False)])
    files, _ = message_files(msg)
    assert files[0].media_type == "image/jpeg"
    assert max(Image.open(io.BytesIO(files[0].data)).size) == 2000


def test_phone_photos_are_turned_the_right_way_up():
    photo = picture(800, 400, exif_orientation=6)
    msg = make_email("photo", attachments=[("IMG_0001.jpg", "image/jpeg", photo, False)])
    files, _ = message_files(msg)
    assert Image.open(io.BytesIO(files[0].data)).size == (400, 800)


def test_small_signature_pictures_are_ignored_in_silence():
    msg = make_email("hi", attachments=[("logo.png", "image/png", picture(40, 20), True)])
    assert message_files(msg) == ([], [])


def test_unsupported_files_are_named_but_cards_and_invites_are_not():
    msg = make_email("hi", attachments=[
        ("report.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", b"PK..", False),
        ("alice.vcf", "text/vcard", b"BEGIN:VCARD", False),
        ("invite.ics", "application/ics", b"BEGIN:VCALENDAR", False),
    ])
    files, skipped = message_files(msg)
    assert files == []
    assert [(s.filename, s.reason) for s in skipped] == [("report.docx", SkipReason.UNSUPPORTED)]


def test_limits_are_told_with_a_reason(pdf):
    msg = make_email("many", attachments=[
        ("fake.pdf", "application/pdf", b"not a pdf", False),
        ("big.pdf", "application/pdf", pdf + b"x" * 2000, False),
        ("a.pdf", "application/pdf", pdf, False),
        ("b.pdf", "application/pdf", pdf, False),
    ])
    files, skipped = message_files(msg, Limits(max_files=1, max_pdf_bytes=1000))
    assert [f.filename for f in files] == ["a.pdf"]
    assert [(s.filename, s.reason) for s in skipped] == [
        ("fake.pdf", SkipReason.UNREADABLE), ("big.pdf", SkipReason.TOO_BIG), ("b.pdf", SkipReason.TOO_MANY),
    ]


def test_a_broken_picture_is_unreadable():
    msg = make_email("hi", attachments=[("broken.jpg", "image/jpeg", b"\xff\xd8" + b"0" * 40000, False)])
    files, skipped = message_files(msg)
    assert files == [] and skipped[0].reason == SkipReason.UNREADABLE
    assert SkipReason.UNREADABLE == "unreadable"  # plain strings for logs and translations
