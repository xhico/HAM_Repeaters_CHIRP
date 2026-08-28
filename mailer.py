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

import html
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


def render_changes(changes, source: str) -> tuple[str, str, str]:
    """
    Render a change report as "(subject, text body, html body)".

    "changes" is the Changes record HAM_Repeaters_CHIRP.build_chirp_csv
    returns. Both bodies carry the same information: mail clients that
    refuse HTML still get the full report.
    """
    subject = f"HAM Repeaters — {changes.summary}"

    lines = [f"Changes since the last run: {changes.summary}", ""]
    for channel in changes.added:
        lines.append(f"+ {channel.callsign:9} {channel.frequency:>10}  {channel.comment}")
    for channel in changes.removed:
        lines.append(f"- {channel.callsign:9} {channel.frequency:>10}  {channel.comment}")
    for channel, fields in changes.modified:
        lines.append(f"~ {channel.callsign:9} {channel.frequency:>10}")
        lines.extend(f"      {field}" for field in fields)
    lines += ["", f"{changes.total} channels in total, from {source}."]
    text_body = "\n".join(lines)

    def row(marker: str, colour: str, channel, detail: str) -> str:
        return (
            f'<tr><td style="padding:4px 10px 4px 0;color:{colour};font-weight:700">{marker}</td>'
            f'<td style="padding:4px 10px 4px 0;font-weight:600">{html.escape(channel.callsign)}</td>'
            f'<td style="padding:4px 10px 4px 0;text-align:right;'
            f'font-variant-numeric:tabular-nums">{html.escape(channel.frequency)}</td>'
            f'<td style="padding:4px 0">{detail}</td></tr>'
        )

    rows = [row("+", "#1a7f37", c, html.escape(c.comment)) for c in changes.added]
    rows += [row("-", "#b42318", c, html.escape(c.comment)) for c in changes.removed]
    rows += [
        row("~", "#9a6700", c, "<br>".join(html.escape(f) for f in fields))
        for c, fields in changes.modified
    ]

    html_body = (
        '<div style="font:14px/1.5 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;'
        'color:#16181d">'
        f"<p style=\"margin:0 0 12px\"><strong>Changes since the last run:</strong> "
        f"{html.escape(changes.summary)}</p>"
        '<table style="border-collapse:collapse;font-size:13px">'
        f"{''.join(rows)}"
        "</table>"
        f'<p style="margin:14px 0 0;color:#6b7280;font-size:12px">'
        f"{changes.total} channels in total, from {html.escape(source)}.</p>"
        "</div>"
    )
    return subject, text_body, html_body
