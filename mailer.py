"""
mailer
======

SMTP delivery for the change report, using nothing but the standard
library, so the container stays dependency-free like the rest of the
project.

Configuration comes from the environment (see .env.example):

    EMAIL_TO, EMAIL_FROM
    SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD
    SMTP_STARTTLS, SMTP_SSL, SMTP_TIMEOUT

Nothing here reads a .env file itself: the service loads that once at
startup and puts the values in the environment.
"""

import os
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

_TRUTHY = frozenset({"1", "true", "yes", "on"})


class MailError(Exception):
    """Delivery failed. The caller decides whether that is fatal."""


def _raw(name: str, default: str = "") -> str:
    value = os.environ.get(name, "")
    return value.strip() or default


def _bool(name: str, default: bool) -> bool:
    value = _raw(name)
    return default if not value else value.casefold() in _TRUTHY


def _int(name: str, default: int) -> int:
    try:
        return int(_raw(name) or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class SmtpConfig:
    """Everything needed to deliver one message."""

    host: str
    port: int
    username: str
    password: str
    use_starttls: bool
    use_ssl: bool
    sender: str
    recipients: list[str] = field(default_factory=list)
    timeout: int = 30

    @property
    def configured(self) -> bool:
        """True when there is somewhere to send and something to send through."""
        return bool(self.host and self.recipients)

    @classmethod
    def from_env(cls) -> "SmtpConfig":
        """Build a config from the environment, filling in sane defaults."""
        recipients = [part.strip() for part in _raw("EMAIL_TO").split(",") if part.strip()]
        sender = _raw("EMAIL_FROM") or (recipients[0] if recipients else "")
        return cls(
            host=_raw("SMTP_HOST"),
            port=_int("SMTP_PORT", 587),
            username=_raw("SMTP_USERNAME"),
            password=_raw("SMTP_PASSWORD"),
            use_starttls=_bool("SMTP_STARTTLS", True),
            use_ssl=_bool("SMTP_SSL", False),
            sender=sender,
            recipients=recipients,
            timeout=_int("SMTP_TIMEOUT", 30),
        )


def _connect(config: SmtpConfig) -> smtplib.SMTP:
    """Open an SMTP connection, honouring SSL or STARTTLS as configured."""
    context = ssl.create_default_context()
    if config.use_ssl:
        server: smtplib.SMTP = smtplib.SMTP_SSL(
            config.host, config.port, timeout=config.timeout, context=context
        )
    else:
        server = smtplib.SMTP(config.host, config.port, timeout=config.timeout)
        server.ehlo()
        if config.use_starttls:
            server.starttls(context=context)
    server.ehlo()
    if config.username:
        server.login(config.username, config.password)
    return server


def verify(config: SmtpConfig) -> bool:
    """
    Probe the server once at startup.

    A weekly service that only mails on change could otherwise sit for a
    month before revealing that its credentials are wrong.
    """
    try:
        with _connect(config):
            pass
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        print(f"[!!] SMTP check failed for {config.host}:{config.port} — {exc}")
        return False
    print(f"[..] SMTP check OK ({config.host}:{config.port})")
    return True


def send(config: SmtpConfig, subject: str, text_body: str, html_body: str) -> None:
    """
    Deliver one message to every recipient.

    Recipients go in Bcc so the delivered copy does not name the whole
    list, with the envelope passed explicitly — left to itself,
    send_message would derive it from To plus Bcc and deliver twice to
    anyone who is both a recipient and the sender.
    """
    mail = EmailMessage()
    mail["Subject"] = subject
    mail["From"] = config.sender
    mail["To"] = config.sender
    mail["Bcc"] = ", ".join(config.recipients)
    mail["Date"] = formatdate(localtime=True)
    mail["Auto-Submitted"] = "auto-generated"
    mail["X-Mailer"] = "HAM_Repeaters_CHIRP"
    _, _, domain = config.sender.rpartition("@")
    mail["Message-ID"] = make_msgid(domain=domain.strip("> ").strip() or "ham-repeaters.local")

    mail.set_content(text_body)
    mail.add_alternative(html_body, subtype="html")

    try:
        with _connect(config) as server:
            server.send_message(mail, to_addrs=config.recipients)
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        raise MailError(f"SMTP delivery failed: {exc}") from exc

    print(f"[OK] Emailed {subject!r} to {len(config.recipients)} recipient(s)")

