import pytest
from conftest import make_email

from flask_inbound_email import Rejection, Verdicts, read_email, rejection


def test_a_forwarded_email_with_a_pdf_is_read_whole(pdf):
    raw = make_email(
        "Pay by Friday\n\n---------- Forwarded message ---------\nFrom: bob@builder.example\nSubject: Invoice\n\nHi",
        sender="Alice Example <Alice@Example.com>", subject="Fwd: Invoice",
        attachments=[("invoice.pdf", "application/pdf", pdf, False)],
    ).as_bytes()
    email = read_email(raw)
    assert email.sender == "alice@example.com"
    assert email.message_id == "<abc123@example.com>"
    assert email.forward.note == "Pay by Friday"
    assert email.forward.original_subject == "Invoice"
    assert [f.filename for f in email.files] == ["invoice.pdf"]
    assert email.invite is None


def test_without_message_id_the_same_text_gives_the_same_key():
    a = read_email(make_email("same words", message_id=None).as_bytes())
    b = read_email(make_email("same words", message_id=None).as_bytes())
    assert a.message_id == b.message_id and len(a.message_id) == 64


def test_a_genuine_email_passes():
    msg = make_email("hi")
    assert rejection(msg, "alice@example.com", Verdicts(), own_domains=["in.example.org"]) is None


@pytest.mark.parametrize("verdicts, headers, sender, expected", [
    (Verdicts(virus="FAIL"), {}, "alice@example.com", Rejection.VIRUS),
    (Verdicts(spam="FAIL"), {}, "alice@example.com", Rejection.SPAM),
    (Verdicts(dmarc="FAIL"), {}, "alice@example.com", Rejection.DMARC_FAILED),
    (Verdicts(spf="FAIL", dkim="GRAY"), {}, "alice@example.com", Rejection.UNAUTHENTICATED),
    (Verdicts(), {"Auto-Submitted": "auto-replied"}, "alice@example.com", Rejection.AUTO_REPLY),
    (Verdicts(), {"Precedence": "bulk"}, "alice@example.com", Rejection.AUTO_REPLY),
    (Verdicts(), {}, "no-reply@shop.example", Rejection.BOUNCE),
    (Verdicts(), {}, "mailer-daemon@example.com", Rejection.BOUNCE),
    (Verdicts(), {}, "bot@in.example.org", Rejection.OWN_DOMAIN),
])
def test_untrusted_emails_are_rejected(verdicts, headers, sender, expected):
    msg = make_email("hi", headers=headers)
    assert rejection(msg, sender, verdicts, own_domains=["in.example.org"]) == expected


def test_one_of_spf_or_dkim_is_enough():
    assert rejection(make_email("hi"), "alice@example.com", Verdicts(spf="FAIL")) is None
