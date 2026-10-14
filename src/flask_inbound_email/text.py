"""Plain text of an email: text/plain preferred, HTML turned into text, the new part of a reply."""

import re
from email.message import EmailMessage
from html.parser import HTMLParser

# Addresses that never belong to a person: answering them only makes loops
NOREPLY = re.compile(r"(no[-_.]?reply|do[-_.]?not[-_.]?reply|mailer-daemon|postmaster)@", re.I)

_REPLY_HEADER = re.compile(
    r"^\s*(Il giorno .+ ha scritto:|On .+ wrote:|-{2,}\s*(Original Message|Messaggio originale)\s*-{2,})\s*$", re.I
)


class _HTMLText(HTMLParser):
    _BLOCK = {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "blockquote"}
    _HIDDEN = {"script", "style", "head"}

    def __init__(self):
        super().__init__()
        self.out, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self._HIDDEN:
            self._skip += 1
        elif tag in self._BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self._HIDDEN and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.out.append(data)


def html_to_text(markup: str) -> str:
    """Readable text of an HTML fragment: blocks become lines, scripts and styles are dropped."""
    parser = _HTMLText()
    parser.feed(markup)
    return re.sub(r"\n\s*\n+", "\n\n", "".join(parser.out)).strip()


def strip_quoted(text: str) -> str:
    """Keep the new part of a reply: drop '>' quotes, the quoted thread and the signature."""
    kept = []
    for line in text.splitlines():
        if _REPLY_HEADER.match(line) or line.rstrip() == "--":
            break
        if line.lstrip().startswith(">"):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def message_text(msg: EmailMessage) -> str:
    """Plain text of a MIME message (text/plain preferred, else text/html); attachments are skipped."""
    plain = msg.get_body(preferencelist=("plain",))
    if plain is not None:
        return plain.get_content()
    rich = msg.get_body(preferencelist=("html",))
    return html_to_text(rich.get_content()) if rich is not None else ""
