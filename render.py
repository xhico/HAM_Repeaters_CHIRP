"""
render
======

The change-report email: subject, plain-text and HTML bodies.

The subject is front-loaded for phone notifications, which cut off around
40 characters: what moved, and how much, before anything else. Bodies are
table-based with inline styles, the only layout that survives Gmail,
Outlook and Apple Mail intact. Both bodies carry the same information, so
a client that refuses HTML still gets the full report.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import chirp_to_html

if TYPE_CHECKING:
    from HAM_Repeaters_CHIRP import Changes, Channel, FieldChange

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

ADDED = "#067647"
REMOVED = "#B42318"
MODIFIED = "#B54708"

BRAND = "#175CD3"
BRAND_TINT = "#EFF8FF"  # section heading band
LINE = "#B8BFCA"  # row dividers and box borders: visible on white, lighter than text
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif"

# The search the scrape starts from. Individual stations link to their own
# record; this is for the footer, where there is no one station to point at.
REGISTRY_URL = "https://www.anacom.pt/saas/pesquisa-eucla.do"

DISTANCE_NOTE = "Distances are in a straight line from home, not by road."
LICENSED_NOTE = "ANACOM records what is licensed, not what is on the air."


@dataclass
class Message:
    subject: str
    html_body: str
    text_body: str


def long_date(day: datetime) -> str:
    """'Friday, 20 November 2026', independent of the container's locale."""
    return f"{WEEKDAYS[day.weekday()]}, {day.day} {MONTHS[day.month - 1]} {day.year}"


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _cadence(hours: float) -> str:
    """'weekly', 'daily', 'every 3 days', 'every 12 h'."""
    if hours == 168:
        return "weekly"
    if hours == 24:
        return "daily"
    if hours % 24 == 0:
        return f"every {hours / 24:g} days"
    return f"every {hours:g} h"


def _f(weight: int, size: int, color: str, extra: str = "") -> str:
    return f"font:{weight} {size}px/1.5 {FONT};color:{color};{extra}"


def _e(text: str) -> str:
    return html.escape(text or "")


def _count_parts(changes: Changes) -> list[str]:
    """['1 added', '2 modified'] — only the kinds that actually happened."""
    parts = []
    if changes.added:
        parts.append(f"{len(changes.added)} added")
    if changes.removed:
        parts.append(f"{len(changes.removed)} removed")
    if changes.modified:
        parts.append(f"{len(changes.modified)} modified")
    return parts


def _change_count(changes: Changes) -> int:
    return len(changes.added) + len(changes.removed) + len(changes.modified)


# ─── Channel details ─────────────────────────────────────────────────────────


def _split_comment(channel: Channel) -> tuple[str, str]:
    """The comment without its trailing distance, and that distance ('83 km')."""
    match = chirp_to_html.COMMENT_DISTANCE_RE.search(channel.comment)
    if not match:
        return channel.comment, ""
    return channel.comment[: match.start()].strip(), f"{float(match.group(1)):.0f} km"


def _where(channel: Channel) -> str:
    """'IM58GQ · RV48 · Rede dos Emissores Portugueses', from the comment."""
    comment, _ = _split_comment(channel)
    return comment.replace(" - ", " · ")


def _settings(channel: Channel, minus: str = "-") -> str:
    """'Shift -0.6 MHz · CTCSS 74.4 Hz': what you program besides the frequency."""
    parts = []
    try:
        offset = float(channel.offset or 0)
    except ValueError:
        offset = 0.0
    if channel.duplex and offset:
        sign = minus if channel.duplex == "-" else channel.duplex
        parts.append(f"Shift {sign}{offset:g} MHz")
    elif not channel.duplex:
        parts.append("Simplex")
    if channel.tone:
        parts.append(f"CTCSS {channel.tone} Hz")
    return " · ".join(parts)


def _subtitle(changes: Changes, source: str, now: datetime) -> str:
    return f"{long_date(now)} · {_plural(changes.total, 'channel', 'channels')} in {source}"


def _title(changes: Changes) -> str:
    return f"{_plural(_change_count(changes), 'change', 'changes')} in the registry"


# ─── Subject ─────────────────────────────────────────────────────────────────


def build_subject(changes: Changes) -> str:
    """'📡 HAM Repeaters · 1 added · 2 modified'."""
    return "📡 HAM Repeaters · " + " · ".join(_count_parts(changes))


# ─── HTML ────────────────────────────────────────────────────────────────────


def _stat(label: str, value: int, color: str) -> str:
    dim = "" if value else "opacity:0.45;"
    return f"""
      <td width="33%" align="center" style="padding:12px 4px;{dim}">
        <div style="font:700 22px/1.1 {FONT};color:{color};">{value}</div>
        <div style="font:400 10px/1.4 {FONT};color:#667085;text-transform:uppercase;letter-spacing:.4px;padding-top:4px;">{_e(label)}</div>
      </td>"""


def _section(title: str, body: str, hint: str = "") -> str:
    """A titled block: tinted heading band with a brand bar, then its rows.

    The band is a table cell with a background and a left border, which every
    mail client renders; CSS pseudo-elements or flex would not survive Outlook.
    """
    hint_html = f'<div style="{_f(400, 12, "#475467", "padding-top:6px;")}">{_e(hint)}</div>' if hint else ""
    return f"""
    <tr><td style="padding:28px 24px 0;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
        <tr><td style="background:{BRAND_TINT};border-left:4px solid {BRAND};border-radius:0 6px 6px 0;padding:9px 12px;font:700 13px/1.2 {FONT};color:#101828;text-transform:uppercase;letter-spacing:.6px;">{_e(title)}</td></tr>
      </table>
      {hint_html}
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:4px;">{body}</table>
    </td></tr>"""


def _callsign_html(channel: Channel) -> str:
    """The callsign, linked to its ANACOM record when the id is known."""
    style = _f(600, 14, "#101828", "line-height:1.4;text-decoration:none;")
    if channel.anacom_id:
        url = chirp_to_html.anacom_url(channel.anacom_id)
        return f'<a href="{_e(url)}" style="{style}">{_e(channel.callsign)}</a>'
    return f'<span style="{style}">{_e(channel.callsign)}</span>'


def _km_html(channel: Channel) -> str:
    """The distance, linked to the site on Google Maps when coordinates are known."""
    _, km = _split_comment(channel)
    if not km:
        return ""
    if not channel.coords:
        return _e(km)
    url = chirp_to_html.maps_url(channel.coords)
    return f'<a href="{_e(url)}" style="color:{BRAND};text-decoration:none;">{_e(km)}</a>'


def _meta(channel: Channel) -> str:
    """Grey detail line: locator, channel and holder, then the distance link if any."""
    text, km = _e(_where(channel)), _km_html(channel)
    return f"{text} · {km}" if text and km else text or km


def _row_head(channel: Channel) -> str:
    """Callsign on the left, frequency on the right."""
    return f"""
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
            <td>{_callsign_html(channel)}</td>
            <td align="right" style="{_f(600, 13, "#101828", "white-space:nowrap;")}">{_e(channel.frequency)} MHz</td>
          </tr></table>"""


def _channel_row(channel: Channel, color: str) -> str:
    settings = _settings(channel, minus="−")
    settings_html = f'<div style="{_f(600, 12, color)}">{_e(settings)}</div>' if settings else ""
    return f"""
        <tr><td style="padding:10px 0;border-bottom:1px solid {LINE};">{_row_head(channel)}
          <div style="{_f(400, 12, "#667085")}">{_meta(channel)}</div>{settings_html}
        </td></tr>"""


def _value_html(value: str) -> str:
    return _e(value) if value else '<em style="font-style:italic;">blank</em>'


def _modified_row(channel: Channel, fields: list[FieldChange]) -> str:
    lines = "".join(
        f"""
            <tr>
              <td valign="top" width="28%" style="padding:3px 8px 3px 0;{_f(400, 12, "#475467")}">{_e(field.name)}</td>
              <td valign="top" style="padding:3px 0;{_f(400, 12, "#667085")}">
                <span style="text-decoration:line-through;">{_value_html(field.old)}</span>
                <span style="color:{MODIFIED};padding:0 4px;">→</span>
                <span style="color:{MODIFIED};font-weight:700;">{_value_html(field.new)}</span>
              </td>
            </tr>"""
        for field in fields
    )
    return f"""
        <tr><td style="padding:10px 0;border-bottom:1px solid {LINE};">{_row_head(channel)}
          <div style="{_f(400, 12, "#667085", "padding-bottom:4px;")}">{_meta(channel)}</div>
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{lines}</table>
        </td></tr>"""


def _sections(changes: Changes) -> list[str]:
    sections = []
    if changes.added:
        rows = "".join(_channel_row(c, ADDED) for c in changes.added)
        sections.append(_section("Added", rows, "Newly licensed, and now in the channel list."))
    if changes.removed:
        rows = "".join(_channel_row(c, REMOVED) for c in changes.removed)
        sections.append(_section("Removed", rows, "No longer in the registry, so dropped from the list."))
    if changes.modified:
        rows = "".join(_modified_row(c, fields) for c, fields in changes.modified)
        sections.append(_section("Modified", rows, "Still licensed, with different settings."))
    return sections


def _shows_distance(changes: Changes) -> bool:
    channels = [*changes.added, *changes.removed, *(c for c, _ in changes.modified)]
    return any(_split_comment(c)[1] for c in channels)


def _footer(changes: Changes, source: str, interval_hours: float | None) -> str:
    distance = f"{DISTANCE_NOTE}<br>" if _shows_distance(changes) else ""
    cadence = f"Checked {_cadence(interval_hours)}; " if interval_hours else ""
    return (
        f"<strong>{_e(LICENSED_NOTE)}</strong> A licensed repeater may be switched off or not yet built.<br>"
        f"{distance}"
        f"{cadence}{_e(source)} is already updated with these changes.<br>"
        f'Source: <a href="{_e(REGISTRY_URL)}" style="color:#667085;">ANACOM licence registry</a> '
        "(Estações de Uso Comum). HAM Repeaters CHIRP."
    )


def _shell(title: str, subtitle: str, preheader: str, inner: str, footer: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"></head>
<body style="margin:0;padding:0;background:#F2F4F7;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{_e(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F2F4F7;padding:24px 12px;">
<tr><td align="center">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background:#FFFFFF;border-radius:14px;overflow:hidden;box-shadow:0 1px 3px rgba(16,24,40,.1);">
    <tr><td style="background:{BRAND};padding:22px 24px;">
      <div style="font:700 12px/1 {FONT};color:#FFFFFF;text-transform:uppercase;letter-spacing:1.2px;opacity:.8;">📡&nbsp;&nbsp;HAM Repeaters</div>
      <div style="font:700 22px/1.25 {FONT};color:#FFFFFF;padding-top:6px;">{_e(title)}</div>
      <div style="font:400 13px/1.5 {FONT};color:#FFFFFF;opacity:.85;padding-top:2px;">{_e(subtitle)}</div>
    </td></tr>
    {inner}
    <tr><td style="padding:20px 24px 0;"></td></tr>
    <tr><td style="padding:14px 24px;background:#F9FAFB;border-top:1px solid {LINE};font:400 11px/1.6 {FONT};color:#98A2B3;">
      {footer}
    </td></tr>
  </table>
</td></tr>
</table>
</body></html>"""


def build_html(changes: Changes, source: str, now: datetime, interval_hours: float | None = None) -> str:
    stats = f"""
    <tr><td style="padding:16px 24px 0;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {LINE};border-radius:10px;">
        <tr>
          {_stat("Added", len(changes.added), ADDED)}
          {_stat("Removed", len(changes.removed), REMOVED)}
          {_stat("Modified", len(changes.modified), MODIFIED)}
        </tr>
      </table>
    </td></tr>"""

    inner = stats + "".join(_sections(changes))
    preheader = " · ".join(_count_parts(changes)) + f" · {changes.total} channels"
    return _shell(
        _title(changes),
        _subtitle(changes, source, now),
        preheader,
        inner,
        _footer(changes, source, interval_hours),
    )


# ─── Plain text ──────────────────────────────────────────────────────────────


def _text_channel(channel: Channel) -> list[str]:
    head = f"  {channel.callsign:9} {channel.frequency:>9} MHz"
    settings = _settings(channel)
    _, km = _split_comment(channel)
    where = " · ".join(part for part in (_where(channel), km) if part)
    return [f"{head}  {settings}" if settings else head] + ([f"    {where}"] if where else [])


def build_text(changes: Changes, source: str, now: datetime, interval_hours: float | None = None) -> str:
    lines = [
        f"HAM REPEATERS — {_title(changes)}",
        _subtitle(changes, source, now),
        "",
        f"Added: {len(changes.added)} · Removed: {len(changes.removed)} · Modified: {len(changes.modified)}",
    ]

    if changes.added:
        lines += ["", "ADDED"]
        for channel in changes.added:
            lines += _text_channel(channel)

    if changes.removed:
        lines += ["", "REMOVED"]
        for channel in changes.removed:
            lines += _text_channel(channel)

    if changes.modified:
        lines += ["", "MODIFIED"]
        for channel, fields in changes.modified:
            lines.append(f"  {channel.callsign:9} {channel.frequency:>9} MHz")
            lines += [f"    {field}" for field in fields]

    lines += ["", LICENSED_NOTE]
    if _shows_distance(changes):
        lines.append(DISTANCE_NOTE)
    if interval_hours:
        lines.append(f"Checked {_cadence(interval_hours)}; {source} is already updated with these changes.")
    lines.append(f"Source: {REGISTRY_URL}")
    return "\n".join(lines)


def build_message(
    changes: Changes, source: str, interval_hours: float | None = None, now: datetime | None = None
) -> Message:
    """
    Render a change report.

    "changes" is the Changes record HAM_Repeaters_CHIRP.build_chirp_csv
    returns; "source" is the CSV it wrote, named in the body so the reader
    knows which file now holds the new list.
    """
    now = now or datetime.now()
    return Message(
        subject=build_subject(changes),
        html_body=build_html(changes, source, now, interval_hours),
        text_body=build_text(changes, source, now, interval_hours),
    )
