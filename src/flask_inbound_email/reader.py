"""One email, from raw bytes to everything an app needs: who sent it, the text, the forwarded
message, the attachments and the calendar invitation."""

import hashlib
from dataclasses import dataclass
from datetime import tzinfo
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import parseaddr

from .attachments import DEFAULT_LIMITS, Attachment, Limits, SkippedFile, message_files
from .forwarded import Forwarded, split_forward
from .invites import Invite, find_calendar, parse_invite
from .text import message_text


@dataclass
class ReceivedEmail:
    sender: str  # the From address, lower case
    subject: str
    message_id: str  # the Message-ID, or a hash of the text: the key against doing the work twice
    text: str
    forward: Forwarded  # the user's note and the forwarded message (forward.is_forward)
    files: list[Attachment]
    skipped: list[SkippedFile]  # attachments that could not be used, to tell the sender
    invite: Invite | None
    message: EmailMessage  # the parsed message itself


def parse_message(raw: bytes) -> EmailMessage:
    return message_from_bytes(raw, policy=policy.default)


def sender_of(msg: EmailMessage) -> str:
    return parseaddr(str(msg.get("From", "")))[1].lower()


def read_email(
    raw: bytes | EmailMessage, *, tz: tzinfo | None = None, limits: Limits = DEFAULT_LIMITS
) -> ReceivedEmail:
    """Read an email. It does not decide whether to trust it: see checks.rejection()."""
    msg = raw if isinstance(raw, EmailMessage) else parse_message(raw)
    subject = str(msg.get("Subject", ""))
    text = message_text(msg)
    files, skipped = message_files(msg, limits)
    calendar = find_calendar(msg)
    message_id = str(msg.get("Message-ID", "")).strip() or hashlib.sha256(text.encode()).hexdigest()
    return ReceivedEmail(
        sender=sender_of(msg),
        subject=subject,
        message_id=message_id[:255],
        text=text,
        forward=split_forward(text, outer_subject=subject),
        files=files,
        skipped=skipped,
        invite=parse_invite(calendar, tz) if calendar else None,
        message=msg,
    )
