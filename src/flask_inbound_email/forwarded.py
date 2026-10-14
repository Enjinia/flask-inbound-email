"""Split a forwarded email into the user's note and the original message.

Recognises the forward markers of Gmail, Apple Mail and Outlook (in Italian and
English) and Outlook's bare "Da:/Inviato:/Oggetto:" header block.
"""

import re
from dataclasses import dataclass

_MARKERS = [
    re.compile(r"^-{3,}\s*(Forwarded message|Messaggio inoltrato)\s*-{3,}\s*$", re.I),  # Gmail
    re.compile(r"^(Begin forwarded message|Inizio messaggio inoltrato)\s*:\s*$", re.I),  # Apple Mail
    re.compile(r"^-{2,}\s*(Original Message|Messaggio originale)\s*-{2,}\s*$", re.I),  # Outlook
    re.compile(r"^_{10,}\s*$"),  # Outlook web/desktop separator line
]
_HEADER = re.compile(
    r"^\s*[*>]*\s*(From|Da|Sent|Inviato|Date|Data|Subject|Oggetto|To|A|Cc|Reply-To|Rispondi a)\s*:[*\s]*(.*?)\s*$",
    re.I,
)
_FROM_KEYS = {"from", "da"}
_SUBJECT_KEYS = {"subject", "oggetto"}
_DATE_KEYS = {"sent", "inviato", "date", "data"}
_SUBJECT_PREFIX = re.compile(r"^\s*((fwd?|fw|i|inoltro|tr|r|re|ris)\s*:\s*)+", re.I)


@dataclass
class Forwarded:
    is_forward: bool
    note: str  # what the user wrote above the forwarded message
    original_from: str = ""
    original_subject: str = ""
    original_date: str = ""
    body: str = ""  # the forwarded message text


def clean_subject(subject):
    return _SUBJECT_PREFIX.sub("", subject or "").strip()


def _is_header_block(lines, i):
    """Outlook without separator: 'Da:' followed shortly by 'Inviato:/Data:' and 'Oggetto:'."""
    m = _HEADER.match(lines[i])
    if not m or m.group(1).lower() not in _FROM_KEYS:
        return False
    keys = {(_HEADER.match(line).group(1).lower() if _HEADER.match(line) else None) for line in lines[i + 1:i + 6]}
    return bool(keys & _DATE_KEYS) and bool(keys & _SUBJECT_KEYS)


def _strip_signature(text):
    out = []
    for line in text.splitlines():
        if line.rstrip() == "--":
            break
        out.append(line)
    return "\n".join(out).strip()


def split_forward(text, outer_subject=""):
    lines = text.splitlines()
    split_at = start = None  # split_at: first line of the forward; start: first header line
    for i, line in enumerate(lines):
        if any(m.match(line) for m in _MARKERS):
            split_at, start = i, i + 1
            break
        if _is_header_block(lines, i):
            split_at = start = i
            break
    if split_at is None:
        return Forwarded(is_forward=False, note=_strip_signature(text), body="")

    note = _strip_signature("\n".join(lines[:split_at]))
    headers, j = {}, start
    while j < len(lines):
        line = lines[j]
        m = _HEADER.match(line)
        if m:
            headers.setdefault(m.group(1).lower(), m.group(2))
            j += 1
        elif not line.strip() and not headers:
            j += 1  # blank lines right after the marker
        else:
            break

    body_lines = lines[j:]
    # Some clients quote the whole forwarded part with '>'
    quoted = [line for line in body_lines if line.strip()]
    if quoted and sum(line.lstrip().startswith(">") for line in quoted) > len(quoted) * 0.8:
        body_lines = [re.sub(r"^\s*>\s?", "", line) for line in body_lines]

    def first(keys):
        return next((v for k, v in headers.items() if k in keys and v), "")

    return Forwarded(
        is_forward=True,
        note=note,
        original_from=first(_FROM_KEYS),
        original_subject=clean_subject(first(_SUBJECT_KEYS)) or clean_subject(outer_subject),
        original_date=first(_DATE_KEYS),
        body="\n".join(body_lines).strip(),
    )
