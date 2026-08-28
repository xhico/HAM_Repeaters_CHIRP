"""
service
=======

Container entry point: rebuild the channel list on a timer and email the
change report whenever ANACOM's registry moves.

    python3 service.py

Configuration is entirely environmental (see .env.example). The important
ones:

    CHIRP_INTERVAL_HOURS   how often to rebuild, default 168 (weekly)
    CHIRP_OUTPUT_DIR       where chirp.csv and chirp.html live, default /data
    CHIRP_RUN_ON_START     rebuild immediately on boot, default true
    EMAIL_TO, SMTP_HOST…   where to send the report

The output directory has to be a persistent volume. The previous CSV *is*
the state: without it every restart looks like a first run and there is
nothing to compare against.

Mail is sent only when something actually changed. A first run — no
previous CSV — never mails, so a fresh container does not announce 139
new repeaters.
"""

import os
import random
import signal
import sys
import threading
import traceback
from datetime import datetime

import HAM_Repeaters_CHIRP as chirp
import mailer

# ANACOM changes slowly and each run costs ~140 requests, so the default
# is weekly. The floor is an hour: anything tighter is pointless against a
# registry measured in months, and rude to a government site.
_DEFAULT_INTERVAL_HOURS = 168
_MIN_INTERVAL_HOURS = 1

# Random buffer added to every sleep, as a fraction of the interval, so a
# fleet of these never lands on ANACOM at the same instant. Added, never
# subtracted, so the configured interval stays a floor.
_JITTER = 0.05

# How long to wait before retrying a failed cycle, rather than sleeping
# the full interval — a transient network failure should not cost a week.
_RETRY_MINUTES = 30

_shutdown = threading.Event()


def _raw(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


def _bool(name: str, default: bool) -> bool:
    value = _raw(name)
    return default if not value else value.casefold() in {"1", "true", "yes", "on"}


def _log(message: str) -> None:
    """Timestamped line on stdout, which is where a container's logs live."""
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}", flush=True)


def _load_env_file() -> None:
    """
    Copy .env into the environment without overriding what is already set.

    Docker passes real environment variables; a .env file is the
    convenience for running the service outside a container. Real
    variables win either way, matching how the main script resolves its
    own position settings.
    """
    for key, value in chirp.read_env_file(chirp.ENV_FILE).items():
        os.environ.setdefault(key, value)


def _interval_seconds() -> int:
    """Seconds between rebuilds, floored at _MIN_INTERVAL_HOURS."""
    try:
        hours = float(_raw("CHIRP_INTERVAL_HOURS", str(_DEFAULT_INTERVAL_HOURS)))
    except ValueError:
        _log(f"[!!] CHIRP_INTERVAL_HOURS is not a number — using {_DEFAULT_INTERVAL_HOURS}h")
        hours = _DEFAULT_INTERVAL_HOURS
    return int(max(hours, _MIN_INTERVAL_HOURS) * 3600)


def _handle_signal(signum, _frame) -> None:
    _log(f"[..] Received {signal.Signals(signum).name} — shutting down after this cycle")
    _shutdown.set()


def _run_once(output_file: str, smtp: mailer.SmtpConfig) -> bool:
    """
    Rebuild the list once and mail the report if anything moved.

    Returns True when the cycle completed, False when it failed and should
    be retried sooner than the full interval.
    """
    try:
        changes = chirp.build_chirp_csv(output_file)
    except (OSError, RuntimeError, ValueError) as exc:
        _log(f"[!!] Rebuild failed: {type(exc).__name__}: {exc}")
        return False
    except Exception:  # noqa: BLE001 - a crash must not kill the loop
        _log("[!!] Rebuild crashed:\n" + traceback.format_exc())
        return False

    if changes.first_run:
        _log("[..] First run — nothing to compare against, so no mail sent")
        return True
    if not changes:
        return True
    if not smtp.configured:
        _log("[!!] Changes found but no EMAIL_TO/SMTP_HOST configured — not mailing")
        return True

    subject, text_body, html_body = mailer.render_changes(changes, os.path.basename(output_file))
    try:
        mailer.send(smtp, subject, text_body, html_body)
    except mailer.MailError as exc:
        # The CSV is already written, so the next cycle would compare
        # against it and consider these changes old news. Say so loudly.
        _log(f"[!!] {exc} — this report will not be resent")
    return True


def main() -> None:
    """Run the rebuild loop until asked to stop."""
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    _load_env_file()

    output_dir = _raw("CHIRP_OUTPUT_DIR", "/data")
    output_file = os.path.join(output_dir, "chirp.csv")
    interval = _interval_seconds()
    smtp = mailer.SmtpConfig.from_env()

    try:
        os.makedirs(output_dir, exist_ok=True)
    except OSError as exc:
        _log(f"[!!] Output directory {output_dir} is not usable: {exc}")
        sys.exit(1)

    _log(f"[..] Writing to {output_file}, rebuilding every {interval / 3600:g}h")
    if smtp.configured:
        _log(f"[..] Reporting changes to {', '.join(smtp.recipients)}")
        mailer.verify(smtp)
    else:
        _log("[!!] EMAIL_TO or SMTP_HOST unset — changes will only be logged")

    if _bool("CHIRP_RUN_ON_START", True):
        ok = _run_once(output_file, smtp)
    else:
        _log("[..] CHIRP_RUN_ON_START is false — waiting for the first interval")
        ok = True

    while not _shutdown.is_set():
        delay = interval if ok else _RETRY_MINUTES * 60
        delay = round(delay * (1 + random.uniform(0, _JITTER)))
        _log(f"[..] Next rebuild in {delay / 3600:.1f}h")
        if _shutdown.wait(delay):
            break
        ok = _run_once(output_file, smtp)

    _log("[..] Stopped")


if __name__ == "__main__":
    main()
