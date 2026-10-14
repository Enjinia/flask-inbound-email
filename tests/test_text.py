from conftest import make_email

from flask_inbound_email import html_to_text, strip_quoted
from flask_inbound_email.text import message_text


def test_html_becomes_lines_without_scripts_or_styles():
    html = "<html><head><style>p{}</style></head><body><p>Hello</p><div>world<br>again</div>" \
           "<script>alert(1)</script></body></html>"
    assert html_to_text(html) == "Hello\nworld\nagain"


def test_plain_text_is_preferred_over_html():
    msg = make_email("plain version", html="<p>rich version</p>")
    assert message_text(msg).strip() == "plain version"


def test_html_only_email_is_read():
    msg = make_email("")
    msg.clear_content()
    msg.set_content("<p>Only <b>HTML</b> here</p>", subtype="html")
    assert message_text(msg) == "Only HTML here"


def test_reply_keeps_only_the_new_part():
    text = "Sounds good, see you then.\n\nOn Mon, 12 Oct 2026, Bob wrote:\n> Shall we meet?\n"
    assert strip_quoted(text) == "Sounds good, see you then."
    assert strip_quoted("Va bene\n> citazione\nciao\n--\nAlice") == "Va bene\nciao"
