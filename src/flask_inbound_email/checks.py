"""Whether an incoming email can be trusted, before anything is read or answered.

An email that fails these checks must be dropped in silence: answering it would confirm that the
address exists, and answering automatic emails makes mail loops.
"""

from dataclasses import dataclass
from email.message import EmailMessage
from enum import Enum

from .text import NOREPLY


class Rejection(str, Enum):
    VIRUS = "virus"
    SPAM = "spam"
    DMARC_FAILED = "dmarc_failed"  # forged sender, most likely
    UNAUTHENTICATED = "unauthenticated"  # neither SPF nor DKIM passed
    AUTO_REPLY = "auto_reply"  # out-of-office, mailing lists, bulk mail
    BOUNCE = "bounce"  # delivery reports and no-reply senders
    OWN_DOMAIN = "own_domain"  # sent by the app itself
    UNKNOWN_ADDRESS = "unknown_address"  # no user behind it, or a token that was replaced
    UNTRUSTED_SENDER = "untrusted_sender"  # not one of the user's own addresses


@dataclass
class Verdicts:
    """Receipt verdicts of the mail server (Amazon SES: PASS / FAIL / GRAY / PROCESSING_FAILED / DISABLED)."""

    spf: str = "PASS"
    dkim: str = "PASS"
    dmarc: str = "PASS"
    spam: str = "PASS"
    virus: str = "PASS"


def rejection(msg: EmailMessage, sender: str, verdicts: Verdicts, own_domains=()) -> Rejection | None:
    """Why the email must be dropped, or None. own_domains: the app's inbound domains."""
    if verdicts.virus == "FAIL":
        return Rejection.VIRUS
    if verdicts.spam == "FAIL":
        return Rejection.SPAM
    if verdicts.dmarc == "FAIL":
        return Rejection.DMARC_FAILED
    if verdicts.spf != "PASS" and verdicts.dkim != "PASS":
        return Rejection.UNAUTHENTICATED
    if msg.get("Auto-Submitted", "no").lower() != "no" or msg.get("Precedence", "").lower() in ("bulk", "junk", "list"):
        return Rejection.AUTO_REPLY
    if msg.get_content_type() == "multipart/report" or NOREPLY.search(sender):
        return Rejection.BOUNCE
    if any(sender.endswith("@" + d.lower()) for d in own_domains):
        return Rejection.OWN_DOMAIN
    return None
