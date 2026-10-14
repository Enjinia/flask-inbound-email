"""The Flask extension: who an email is for, whether to trust it, and handing it to the app.

It owns no database table. The app tells it, through a few callbacks, how to find a user from
the token in the address, which addresses belong to that user, and what to do with an email:

    inbound = InboundEmail(app)

    @inbound.user_loader
    def load_user(token):              # None for unknown or suspended users
        return User.query.filter_by(inbound_token=token).first()

    @inbound.trusted_senders
    def senders(user):                 # only these may write to the user's address
        return {user.email}

    @inbound.handler
    def handle(user, email):           # email: ReceivedEmail
        ...                            # raise TemporaryFailure to have it delivered again later
        return Result("handled")

Safety rules kept here, on purpose not configurable: only the user's own addresses are accepted,
and rejected emails are never answered (no mail loops, no confirmation that an address exists).
"""

import logging
import secrets
import string
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from flask import Flask, current_app

from .attachments import DEFAULT_LIMITS, Limits
from .checks import Rejection, Verdicts, rejection
from .reader import parse_message, read_email, sender_of

logger = logging.getLogger(__name__)

REJECTED = "rejected"
_TOKEN_CHARS = string.ascii_lowercase + string.digits


class TemporaryFailure(Exception):
    """The handler cannot work now (an API down, a quota reached...): the email is tried again later."""


@dataclass
class Result:
    outcome: str  # "rejected" (dropped, never answered) or whatever the app says, e.g. "handled"
    reason: str = ""
    extra: dict = field(default_factory=dict)


def new_token(hint: str = "") -> str:
    """Readable but unguessable local part: <hint>-<10 random chars>, e.g. from the user's email.
    Store it on the user; a new one makes the old address stop working."""
    slug = "".join(c for c in hint.split("@")[0].lower() if c in _TOKEN_CHARS)[:12] or "inbox"
    return f"{slug}-{''.join(secrets.choice(_TOKEN_CHARS) for _ in range(10))}"


class InboundEmail:
    def __init__(self, app: Flask | None = None, *, limits: Limits = DEFAULT_LIMITS):
        self.limits = limits
        self._user_loader = None
        self._trusted_senders = None
        self._handler = None
        self._notifier = None
        self._recorder = None
        self._user_context = None
        self._user_timezone = None
        if app is not None:
            self.init_app(app)

    def init_app(self, app: Flask) -> None:
        app.config.setdefault("INBOUND_DOMAIN", None)
        app.config.setdefault("INBOUND_LEGACY_DOMAINS", [])
        app.config.setdefault("INBOUND_TIMEZONE", "UTC")
        app.config.setdefault("INBOUND_QUEUE_URL", None)
        app.config.setdefault("INBOUND_AWS_REGION", "eu-west-1")
        app.extensions["inbound_email"] = self
        from .cli import inbound_cli

        app.cli.add_command(inbound_cli)

    # --- callbacks, usable as decorators --------------------------------------------------------

    def user_loader(self, fn: Callable):
        """fn(token) -> user or None. Return None for suspended users too: as if the address didn't exist."""
        self._user_loader = fn
        return fn

    def trusted_senders(self, fn: Callable):
        """fn(user) -> iterable of the addresses the user may write from (verified ones only)."""
        self._trusted_senders = fn
        return fn

    def handler(self, fn: Callable):
        """fn(user, email) -> Result (None counts as Result("handled"))."""
        self._handler = fn
        return fn

    def notifier(self, fn: Callable):
        """fn(user, email, result): answer who wrote, e.g. "done". Never called for rejected emails;
        its errors are only logged. Mark the answer with the header Auto-Submitted: auto-replied."""
        self._notifier = fn
        return fn

    def recorder(self, fn: Callable):
        """fn(user_or_None, sender, subject, result): keep a log of every email, rejected ones included."""
        self._recorder = fn
        return fn

    def user_context(self, fn: Callable):
        """fn(user) -> context manager around handler and notifier, e.g. Flask-Babel's
        force_locale(user.language) so that what the app writes is in the user's language."""
        self._user_context = fn
        return fn

    def user_timezone(self, fn: Callable):
        """fn(user) -> tzinfo or zone name, for calendar invites. Default: INBOUND_TIMEZONE."""
        self._user_timezone = fn
        return fn

    # --- addresses ------------------------------------------------------------------------------

    @property
    def domains(self) -> set[str]:
        """The current inbound domain plus older ones still accepted."""
        cfg = current_app.config
        return {d.lower() for d in [cfg["INBOUND_DOMAIN"], *cfg["INBOUND_LEGACY_DOMAINS"]] if d}

    def address_for(self, token: str | None) -> str | None:
        domain = current_app.config["INBOUND_DOMAIN"]
        return f"{token}@{domain}" if domain and token else None

    def _recipient(self, recipients):
        domains = self.domains
        for rcpt in recipients:
            local, _, domain = rcpt.lower().partition("@")
            if domain in domains:
                user = self._user_loader(local)
                if user is not None:
                    return user
        return None

    # --- processing -----------------------------------------------------------------------------

    def process(self, raw: bytes, *, recipients, verdicts: Verdicts | None = None, notify: bool = True) -> Result:
        """Process one received email. TemporaryFailure from the handler propagates (retry later)."""
        if self._user_loader is None or self._trusted_senders is None or self._handler is None:
            raise RuntimeError("InboundEmail needs user_loader, trusted_senders and handler")
        msg = parse_message(raw)
        sender = sender_of(msg)
        subject = str(msg.get("Subject", ""))

        user = self._recipient(recipients)
        if user is None:
            return self._record(None, sender, subject, Result(REJECTED, Rejection.UNKNOWN_ADDRESS.value))
        reason = rejection(msg, sender, verdicts or Verdicts(), own_domains=self.domains)
        if reason is None and sender not in {a.lower() for a in self._trusted_senders(user)}:
            reason = Rejection.UNTRUSTED_SENDER
        if reason:
            return self._record(user, sender, subject, Result(REJECTED, reason.value))

        email = read_email(msg, tz=self._timezone(user), limits=self.limits)
        with self._context(user):
            result = self._handler(user, email) or Result("handled")
        self._record(user, sender, subject, result)

        if notify and self._notifier is not None and result.outcome != REJECTED:
            try:
                with self._context(user):
                    self._notifier(user, email, result)
            except Exception as e:  # mail server down or not configured: the work is done anyway
                logger.warning("Inbound email: answer to %s failed: %s", sender, e)
        return result

    def _record(self, user, sender, subject, result):
        if self._recorder is not None:
            self._recorder(user, sender, subject, result)
        return result

    def _context(self, user):
        return self._user_context(user) if self._user_context else nullcontext()

    def _timezone(self, user):
        tz = self._user_timezone(user) if self._user_timezone else None
        tz = tz or current_app.config["INBOUND_TIMEZONE"]
        return ZoneInfo(tz) if isinstance(tz, str) else tz
