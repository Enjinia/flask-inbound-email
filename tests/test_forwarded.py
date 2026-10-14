import pytest

from flask_inbound_email import clean_subject, split_forward

GMAIL_EN = """Please pay this by Friday.

---------- Forwarded message ---------
From: Bob Builder <bob@builder.example>
Date: Mon, 12 Oct 2026 at 09:15
Subject: Invoice 42
To: Alice <alice@example.com>

Dear Alice,
the invoice is attached.
"""

GMAIL_IT = """Da pagare entro venerdì

---------- Messaggio inoltrato ---------
Da: Bob Builder <bob@builder.example>
Date: lun 12 ott 2026 alle ore 09:15
Oggetto: Fattura 42
A: Alice <alice@example.com>

Gentile Alice,
in allegato la fattura.
"""

APPLE = """Look at this

Begin forwarded message:

From: Bob Builder <bob@builder.example>
Subject: Re: Invoice 42
Date: 12 October 2026 at 09:15:00 CEST
To: Alice <alice@example.com>

The invoice is attached.
"""

OUTLOOK_BLOCK = """Can you call him?

Da: Bob Builder <bob@builder.example>
Inviato: lunedì 12 ottobre 2026 09:15
A: Alice <alice@example.com>
Oggetto: I: Preventivo

Buongiorno, ecco il preventivo.
"""

QUOTED = """See below

---------- Forwarded message ---------
From: bob@builder.example
Subject: Meeting

> We meet on Tuesday.
> Bring the plans.
"""


@pytest.mark.parametrize("text, sender, subject, body_start, note", [
    (GMAIL_EN, "Bob Builder <bob@builder.example>", "Invoice 42", "Dear Alice", "Please pay this by Friday."),
    (GMAIL_IT, "Bob Builder <bob@builder.example>", "Fattura 42", "Gentile Alice", "Da pagare entro venerdì"),
    (APPLE, "Bob Builder <bob@builder.example>", "Invoice 42", "The invoice", "Look at this"),
    (OUTLOOK_BLOCK, "Bob Builder <bob@builder.example>", "Preventivo", "Buongiorno", "Can you call him?"),
])
def test_forward_is_split_into_note_and_original(text, sender, subject, body_start, note):
    fwd = split_forward(text)
    assert fwd.is_forward
    assert fwd.note == note
    assert fwd.original_from == sender
    assert fwd.original_subject == subject
    assert fwd.body.startswith(body_start)
    assert fwd.original_date


def test_a_forward_quoted_with_arrows_is_unquoted():
    fwd = split_forward(QUOTED)
    assert fwd.body == "We meet on Tuesday.\nBring the plans."


def test_a_plain_note_is_not_a_forward_and_loses_its_signature():
    fwd = split_forward("Buy milk tomorrow\n\n--\nAlice\nSent from my phone")
    assert not fwd.is_forward
    assert fwd.note == "Buy milk tomorrow"
    assert fwd.body == ""


def test_subject_falls_back_to_the_outer_one():
    fwd = split_forward("---------- Forwarded message ---------\nFrom: bob@builder.example\n\nHi", "Fwd: Plans")
    assert fwd.original_subject == "Plans"


@pytest.mark.parametrize("subject, clean", [
    ("Fwd: Re: Invoice", "Invoice"),
    ("I: R: Fattura", "Fattura"),
    ("TR: Facture", "Facture"),
    ("Invoice", "Invoice"),
    (None, ""),
])
def test_clean_subject(subject, clean):
    assert clean_subject(subject) == clean
