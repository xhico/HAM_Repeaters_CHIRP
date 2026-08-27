"""
HAM_Repeaters_CHIRP
===================

Merge the public Portuguese repeater lists into a single CHIRP-compatible
CSV. Everything happens in memory; the merged CSV is the only file written.

Four feeds are queried per run, in this order:

    1. https://repetidores.pt/json_vhf.php          (DataTables JSON)
    2. https://repetidores.pt/json_uhf.php          (DataTables JSON)
    3. https://portaldoradioamador.pt/...           (CHIRP CSV)
    4. https://api.radioamador.info/api/repeaters   (PayloadCMS JSON)

Order matters: dedup is first-occurrence-wins, so earlier feeds take
precedence when several publish the same (Name, Frequency).

A feed that is down or returns garbage is logged and skipped; the run
only fails outright when every feed is unusable.
"""

# Standard library only, so this runs on a stock Python 3.10+ interpreter.
import csv
import http.client
import io
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import TypeAlias, cast

# A CHIRP row. CHIRP CSVs are text-only, so every column is a string —
# even numeric ones ("145.6000", "0.600000").
Row: TypeAlias = dict[str, str]

# repetidores.pt also serves a ready-made CHIRP CSV at /gerarcsv.php, but
# these two JSON feeds carry the same 87 repeaters plus bandwidth, QTH
# locator, ERP and owner. Its json_dmr/json_dstar feeds are deliberately
# skipped: digital-only repeaters can't be programmed as analogue FM.
# ?limit=500 defeats the radioamador API's default page size of 10.
_REPETIDORES_PT_VHF_URL = "https://repetidores.pt/json_vhf.php"
_REPETIDORES_PT_UHF_URL = "https://repetidores.pt/json_uhf.php"
_PORTALDORADIOAMADOR_PT_URL = "https://portaldoradioamador.pt/backend/repeaters/export/chirp/"
_RADIOAMADOR_INFO_URL = "https://api.radioamador.info/api/repeaters?limit=500"

# Every upstream 403s the default "Python-urllib/X.Y" User-Agent.
_HTTP_USER_AGENT = "Mozilla/5.0 (HAM_Repeaters_CHIRP)"
_HTTP_TIMEOUT_SECONDS = 30

# "This feed gave us nothing usable" — caught per source so one flaky
# upstream can't sink the run. Anything else propagates as a bug.
_SOURCE_ERRORS: tuple[type[Exception], ...] = (
    OSError,  # URLError, HTTPError, TimeoutError and socket failures
    http.client.HTTPException,  # truncated bodies; NOT an OSError
    json.JSONDecodeError,  # error page served where JSON was expected
    UnicodeDecodeError,  # body isn't UTF-8
    csv.Error,  # malformed CSV
)

_CHIRP_HEADER: tuple[str, ...] = (
    "Location", "Name", "Frequency", "Duplex", "Offset",
    "Tone", "rToneFreq", "cToneFreq", "DtcsCode", "DtcsPolarity",
    "Mode", "TStep", "Skip", "Comment",
    "URCALL", "RPT1CALL", "RPT2CALL", "DVCODE",
)

# The csv module defaults to CRLF (RFC 4180), which makes git — configured
# here with "* text=auto" — normalize on commit and then warn that the
# working copy differs. LF keeps tree and repo identical; CHIRP takes either.
_CSV_LINE_TERMINATOR = "\n"

# Columns the pipeline writes to. They must exist in the output header even
# if no source supplied them, or DictWriter(extrasaction="ignore") would
# silently drop our writes.
_REQUIRED_FIELDS: tuple[str, ...] = (
    "Tone", "rToneFreq", "cToneFreq", "TStep", "DVCODE", "Location",
)

# CHIRP refuses to parse these columns when blank. 88.5 Hz is the most
# common CTCSS tone; 12.50 kHz matches the Portuguese narrowband plan.
_DEFAULT_TONE_FREQ = "88.5"
_DEFAULT_TSTEP = "12.50"

# Bands the radio accepts, MHz, inclusive. Anything else is dropped, since
# CHIRP rejects it with "Frequency X is out of supported ranges ...".
_SUPPORTED_FREQ_RANGES: tuple[tuple[float, float], ...] = (
    (65.0, 108.0),
    (136.0, 174.0),
    (400.0, 480.0),
)


# ---------------------------------------------------------------------
# HTTP and source fetchers
# ---------------------------------------------------------------------


def _http_get(url: str) -> bytes:
    """GET "url" and return the raw body. Raises urllib.error.URLError."""
    request = urllib.request.Request(url, headers={"User-Agent": _HTTP_USER_AGENT})
    with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
        return response.read()


def _chirp_row(name: str, out_f: float, in_f: float, tone: str, mode: str, comment: str) -> Row:
    """
    Build a CHIRP row from an adapted source record.

    "out_f" is the repeater's output (what the radio receives) and "in_f"
    its input (what the radio transmits); CHIRP wants that as a magnitude
    plus a direction. "Location" is filled in later by _renumber_locations.
    """
    if out_f > in_f:
        duplex = "-"
    elif out_f < in_f:
        duplex = "+"
    else:
        duplex = ""

    return {
        "Location": "",
        "Name": name,
        "Frequency": f"{out_f:.4f}",
        "Duplex": duplex,
        "Offset": f"{abs(out_f - in_f):.6f}",
        "Tone": "Tone",
        "rToneFreq": tone,
        "cToneFreq": tone,
        "DtcsCode": "023",
        "DtcsPolarity": "NN",
        "Mode": mode,
        "TStep": _DEFAULT_TSTEP,
        "Skip": "",
        "Comment": comment,
        "URCALL": "",
        "RPT1CALL": "",
        "RPT2CALL": "",
        "DVCODE": "",
    }


def _fetch_chirp_csv(url: str) -> tuple[list[Row], list[str]]:
    """Download a CHIRP-format CSV and return "(rows, fieldnames)"."""
    reader = csv.DictReader(io.StringIO(_http_get(url).decode("utf-8")))
    fieldnames = list(reader.fieldnames or [])
    rows = list(reader)
    print(f"[..] Fetched {len(rows)} rows from {url}")
    return rows, fieldnames


def _repetidores_entry_to_row(entry: list) -> Row | None:
    """
    Map one repetidores.pt "aaData" record to a CHIRP row.

    DataTables responses carry no field names, just a fixed 14-column array
    (positions confirmed against both the VHF and UHF feeds):

        0 channel  1 output MHz  2 input MHz  3 CTCSS Hz  4 Narrow/Wide
        5 lat      6 lon         7 locator    8 site      9 callsign
        10 watts   11 owner      12 reports   13 health score

    Returns None for a short record (the feed changed shape — better to
    drop it than emit garbage), a missing tone, or unparseable frequencies.
    """
    if len(entry) < 14:
        return None

    # A blank or "-" tone means carrier access only; _normalize_row would
    # drop the row anyway, so bail out rather than invent a default tone.
    tone_raw = str(entry[3]).strip()
    if not tone_raw or tone_raw == "-":
        return None

    try:
        out_f = float(entry[1])
        in_f = float(entry[2])
        tone = f"{float(tone_raw):.1f}"
    except (TypeError, ValueError):
        return None

    # Narrow is the 12.5 kHz spacing the Portuguese plan uses — CHIRP calls
    # it NFM. Sending wide FM to a narrowband repeater over-deviates.
    mode = "NFM" if str(entry[4]).strip().lower() == "narrow" else "FM"

    # Site name first, matching the site's own CHIRP export, then the
    # extras that export drops.
    comment = " - ".join(bit for bit in (str(entry[8]).strip(), str(entry[7]).strip(), str(entry[11]).strip()) if bit)

    return _chirp_row(str(entry[9]).strip(), out_f, in_f, tone, mode, comment)


def _fetch_repetidores_json(url: str) -> tuple[list[Row], list[str]]:
    """Download a repetidores.pt json_*.php feed and adapt it to CHIRP rows."""
    payload = json.loads(_http_get(url).decode("utf-8"))
    rows: list[Row] = []
    for entry in payload.get("aaData", []):
        row: Row | None = _repetidores_entry_to_row(entry) if isinstance(entry, list) else None
        if row is not None:
            rows.append(row)
    print(f"[..] Fetched {len(rows)} rows from {url}")
    return rows, list(_CHIRP_HEADER)


def _radioamador_repeater_to_row(entry: dict) -> Row | None:
    """
    Map one radioamador.info "docs" entry to a CHIRP row.

    Returns None for anything unusable on an analogue radio: digital-only
    modes, no primary frequency pair, no CTCSS tone, bad frequencies.
    """
    # The dataset also holds DMR/DSTAR/C4FM/TETRA-only entries.
    if "FM" not in (entry.get("modes") or []):
        return None

    # A repeater may publish several pairs (e.g. cross-band); the one
    # flagged isPrimary is canonical, the rest are auxiliary.
    primary: dict | None = next(
        (
            f
            for f in (entry.get("frequencies") or [])
            if isinstance(f, dict) and f.get("isPrimary")
        ),
        None,
    )
    if primary is None:
        return None

    try:
        out_f = float(primary["outputFrequency"])
        in_f = float(primary["inputFrequency"])
    except (KeyError, TypeError, ValueError):
        return None

    # The API uses null for "no tone"; this also catches 0 and "". The cast
    # placates the type checker, which won't narrow Any | None via truthiness.
    tone_raw = primary.get("tone")
    if not tone_raw:
        return None
    try:
        tone = f"{float(cast(float, tone_raw)):.1f}"
    except (TypeError, ValueError):
        return None

    # Locator and association abbreviation, both optional, are handy when
    # browsing the merged list in CHIRP.
    association = entry.get("association") or {}
    comment = " - ".join(
        str(bit) for bit in (entry.get("qth_locator"), association.get("abbreviation")) if bit
    )

    return _chirp_row(
        str(entry.get("callsign", "")).strip(), out_f, in_f, tone, "FM", comment
    )


def _fetch_radioamador_json(url: str) -> tuple[list[Row], list[str]]:
    """Download the radioamador.info API and adapt it to CHIRP rows."""
    payload = json.loads(_http_get(url).decode("utf-8"))
    rows: list[Row] = []
    for entry in payload.get("docs", []):
        row: Row | None = _radioamador_repeater_to_row(entry)
        if row is not None:
            rows.append(row)
    print(f"[..] Fetched {len(rows)} FM rows from {url}")
    return rows, list(_CHIRP_HEADER)


def _download_and_merge() -> tuple[list[Row], list[str]]:
    """
    Fetch every source in order and concatenate the rows.

    The returned fieldnames are the union of every responding source's
    columns, ordered by first appearance, with _REQUIRED_FIELDS appended if
    missing. A source that fails is logged and skipped; RuntimeError is
    raised only if they all fail.
    """
    rows: list[Row] = []
    fieldnames: list[str] = []
    seen_fields: set[str] = set()
    failed: list[str] = []

    # Adding a source means appending here and, unless it already serves
    # CHIRP CSV, writing a matching adapter.
    sources: tuple[tuple[Callable[[str], tuple[list[Row], list[str]]], str], ...] = (
        (_fetch_repetidores_json, _REPETIDORES_PT_VHF_URL),
        (_fetch_repetidores_json, _REPETIDORES_PT_UHF_URL),
        (_fetch_chirp_csv, _PORTALDORADIOAMADOR_PT_URL),
        (_fetch_radioamador_json, _RADIOAMADOR_INFO_URL),
    )

    for fetch, url in sources:
        try:
            src_rows, src_fields = fetch(url)
        except _SOURCE_ERRORS as exc:
            print(f"[!!] Skipping {url}: {type(exc).__name__}: {exc}")
            failed.append(url)
            continue

        for name in src_fields:
            if name not in seen_fields:
                seen_fields.add(name)
                fieldnames.append(name)
        rows.extend(src_rows)

    if failed:
        # Failing here beats a confusing "no tone-enabled rows" further down.
        if len(failed) == len(sources):
            raise RuntimeError(
                "All repeater sources failed to download; see the [!!] lines above. "
                "Output file not generated."
            )
        print(f"[!!] {len(failed)} of {len(sources)} sources unavailable — merging the rest.")

    for required in _REQUIRED_FIELDS:
        if required not in seen_fields:
            seen_fields.add(required)
            fieldnames.append(required)

    return rows, fieldnames


# ---------------------------------------------------------------------
# Normalization, filtering, dedup
# ---------------------------------------------------------------------


def _normalize_row(row: Row) -> Row | None:
    """Normalize a row in place, or return None to drop it."""
    # Analogue repeaters without a CTCSS tone aren't part of this workflow.
    if row.get("Tone", "").strip() == "":
        return None

    # CHIRP can reject mismatched tone pairs, so keep both fields in step.
    ctone = row.get("cToneFreq", "").strip()
    if ctone:
        row["rToneFreq"] = ctone
    row["Tone"] = "Tone"

    if not row.get("rToneFreq", "").strip():
        row["rToneFreq"] = _DEFAULT_TONE_FREQ
    if not row.get("cToneFreq", "").strip():
        row["cToneFreq"] = _DEFAULT_TONE_FREQ
    if not row.get("TStep", "").strip():
        row["TStep"] = _DEFAULT_TSTEP

    # Analogue only.
    row["DVCODE"] = ""
    return row


def _normalize_rows(rows: list[Row]) -> list[Row]:
    """Normalize every row, dropping the ones _normalize_row rejects."""
    normalized: list[Row] = []
    for row in rows:
        kept: Row | None = _normalize_row(row)
        if kept is not None:
            normalized.append(kept)
    return normalized


def _row_frequency(row: Row) -> float:
    """
    Return "Frequency" as a float, or 0.0 if it is missing or malformed.

    0.0 is outside every supported band and sorts to the top, so a bad row
    stays visible instead of silently disappearing.
    """
    try:
        return float(row.get("Frequency", "0") or 0)
    except ValueError:
        return 0.0


def _filter_by_supported_frequency(rows: list[Row]) -> list[Row]:
    """Drop rows outside every band in _SUPPORTED_FREQ_RANGES."""
    kept = [
        row for row in rows
        if any(low <= _row_frequency(row) <= high for low, high in _SUPPORTED_FREQ_RANGES)
    ]
    dropped = len(rows) - len(kept)
    if dropped:
        bands = ", ".join(f"{low}-{high}MHz" for low, high in _SUPPORTED_FREQ_RANGES)
        print(f"[..] Removed {dropped} row(s) outside supported ranges {bands}")
    return kept


def _dedupe_by_name_frequency(rows: list[Row]) -> list[Row]:
    """
    Deduplicate by "(Name, float(Frequency))", first occurrence wins.

    Comparing the frequency as a float collapses "51.9900" and "51.990000".
    Since sources are merged in order, the earliest source's row wins.
    """
    seen: set[tuple[str, float]] = set()
    deduped: list[Row] = []
    for row in rows:
        key = (row.get("Name", "").strip(), _row_frequency(row))
        if key not in seen:
            seen.add(key)
            deduped.append(row)
    duplicates = len(rows) - len(deduped)
    if duplicates:
        print(f"[..] Removed {duplicates} duplicate row(s) by (Name, Frequency)")
    return deduped


def _renumber_locations(rows: list[Row]) -> None:
    """
    Rewrite "Location" as a contiguous 0-based index, in place.

    Each source numbers its own channels from 1, so a plain concatenation
    collides.
    """
    for i, row in enumerate(rows):
        row["Location"] = str(i)


def _write_csv(output_file: str, fieldnames: list[str], rows: list[Row]) -> None:
    """Write "rows" to "output_file", overwriting it."""
    # newline="" stops the text layer translating line endings, so the
    # writer's own lineterminator is what reaches disk.
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
            restval="",
            extrasaction="ignore",
            lineterminator=_CSV_LINE_TERMINATOR,
        )
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------
# Orchestrator + entry point
# ---------------------------------------------------------------------


def clean_chirp_csvs(output_file: str) -> None:
    """
    Download, merge and normalize every source, then write "output_file".

    The pipeline: download and concatenate -> drop tone-less rows and fill
    CHIRP's mandatory numeric columns -> drop out-of-band frequencies ->
    dedupe by (Name, Frequency) -> sort by frequency -> renumber Location.

    Raises ValueError if nothing survives filtering (better than clobbering
    an existing CSV with an empty one), or RuntimeError if every source is
    unreachable.
    """
    rows, fieldnames = _download_and_merge()
    rows = _normalize_rows(rows)
    rows = _filter_by_supported_frequency(rows)
    rows = _dedupe_by_name_frequency(rows)
    rows.sort(key=_row_frequency)
    _renumber_locations(rows)

    if not rows:
        raise ValueError("No valid tone-enabled rows found. Output file not generated.")

    _write_csv(output_file, fieldnames, rows)
    print(f"[OK] Merged {len(rows)} rows into {output_file}")


def main() -> None:
    """Entry point: merge every source into "chirp.csv"."""
    clean_chirp_csvs(output_file="chirp.csv")


if __name__ == "__main__":
    main()
