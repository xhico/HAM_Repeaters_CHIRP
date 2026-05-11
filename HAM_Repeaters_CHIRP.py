"""
HAM_Repeaters_CHRIP
===================

Download repeater lists from public Portuguese amateur-radio sources,
merge, and normalize them into a single CHIRP-compatible CSV suitable for
programming an analogue radio.

The script fetches each remote source over HTTPS, parses it into CHIRP
rows in memory, merges them, drops rows that do not carry a CTCSS/tone
configuration, deduplicates by "(Name, Frequency)", sorts the result by
"Frequency" ascending, renumbers the "Location" column, and writes the
merged file. Nothing is written to disk except the final merged CSV.

Three sources are queried on every run, in declaration order:

    1. "https://repetidores.pt/gerarcsv.php" — CHIRP-format CSV.
    2. "https://portaldoradioamador.pt/backend/repeaters/export/chirp/" — CHIRP-format CSV.
    3. "https://api.radioamador.info/api/repeaters?limit=500" — JSON API,
       adapted into CHIRP rows by "_radioamador_repeater_to_row".

The ordering matters: deduplication is "first occurrence wins", so the
source that appears first in the list takes precedence when the same
"(Name, Frequency)" is reported by more than one upstream.
"""

# Standard-library only — this script intentionally has no third-party
#  dependencies, so it can run on a stock Python 3.10+ interpreter.
import csv  # Parses CHIRP-format CSV downloads and writes the merged CSV.
import io  # Wraps the downloaded CSV bytes into a file-like object for csv.DictReader.
import json  # Decodes the radioamador.info JSON payload.
import os  # Creates the snapshot directory and joins snapshot paths.
import urllib.request  # Stdlib HTTP client (no "requests" dependency).
from typing import TypeAlias, cast

# Type alias for a single CHIRP row. CHIRP CSVs are text-only, so every
# column is a string — even numeric fields like "Frequency" are stored
# as their decimal string representation ("145.6000", "0.600000", ...).
# The explicit "TypeAlias" tag ensures static analysers treat this as a
# type alias rather than a plain module-level attribute.
Row: TypeAlias = dict[str, str]

# ---------------------------------------------------------------------
# Remote sources
# ---------------------------------------------------------------------
# Remote sources to merge. "csv" sources are parsed as CHIRP-format CSV
# directly; "json" sources are mapped through a source-specific adapter.
# The ?limit=500 query string on the radioamador endpoint asks the API
# to return every record in a single response (its default page size is
# 10, which would otherwise force us to paginate).
_REPETIDORES_PT_URL = "https://repetidores.pt/gerarcsv.php"
_PORTALDORADIOAMADOR_PT_URL = "https://portaldoradioamador.pt/backend/repeaters/export/chirp/"
_RADIOAMADOR_INFO_URL = "https://api.radioamador.info/api/repeaters?limit=500"

# Per-source snapshot filenames written into "_CHIRP_FILES_DIR" on every
#  run, so changes between runs are visible as a git diff. The two CSV
# sources are saved verbatim; the JSON source is saved as the adapted
# CHIRP CSV, so all three snapshots share a schema.
_REPETIDORES_PT_SNAPSHOT = "repetidores.csv"
_PORTALDORADIOAMADOR_PT_SNAPSHOT = "portalradioamador.csv"
_RADIOAMADOR_INFO_SNAPSHOT = "radioamador.csv"

# Directory that holds the per-source snapshots. Created on demand if it
# does not yet exist.
_CHIRP_FILES_DIR = "chirp_files"

# ---------------------------------------------------------------------
# HTTP request settings
# ---------------------------------------------------------------------
# Every upstream server in the sources list above rejects the default
# "Python-urllib/X.Y" User-Agent with HTTP 403 Forbidden. We send a
# generic browser-style UA on every request to get past that filter.
_HTTP_USER_AGENT = "Mozilla/5.0 (HAM_Repeaters_CHIRP)"

# Per-request timeout, in seconds. Generous enough to tolerate a slow
# response from any source, but short enough that a hanging server does
# not block the run indefinitely.
_HTTP_TIMEOUT_SECONDS = 30

# ---------------------------------------------------------------------
# CHIRP schema
# ---------------------------------------------------------------------
# Canonical CHIRP CSV header. Used when synthesizing rows from non-CSV
# sources (e.g. the radioamador.info JSON API) so they line up with the
# columns produced by the CHIRP-format CSV exports.
_CHIRP_HEADER: tuple[str, ...] = (
    "Location", "Name", "Frequency", "Duplex", "Offset",
    "Tone", "rToneFreq", "cToneFreq", "DtcsCode", "DtcsPolarity",
    "Mode", "TStep", "Skip", "Comment",
    "URCALL", "RPT1CALL", "RPT2CALL", "DVCODE",
)

# Fields the pipeline writes to. They must always exist in the output
# header, even if none of the input CSVs contained them — otherwise the
# DictWriter would silently drop our writes (extrasaction="ignore").
_REQUIRED_FIELDS: tuple[str, ...] = (
    "Tone",
    "rToneFreq",
    "cToneFreq",
    "TStep",
    "DVCODE",
    "Location",
)

# Safe defaults for numeric columns that CHIRP refuses to parse blank.
# 88.5 Hz is the most common CTCSS tone worldwide and a sensible
# fallback. 12.50 kHz matches the narrowband channel spacing used by
# the Portuguese repeater plan.
_DEFAULT_TONE_FREQ = "88.5"
_DEFAULT_TSTEP = "12.50"

# Supported radio frequency ranges, in MHz, inclusive on both ends. Rows
# whose "Frequency" falls outside every band are dropped, since CHIRP
# (and the target radio) will reject them with errors like:
#     "Frequency 51.990000 is out of supported ranges
#      65-108MHz, 136-174MHz, 400-480MHz"
_SUPPORTED_FREQ_RANGES: tuple[tuple[float, float], ...] = (
    (65.0, 108.0),
    (136.0, 174.0),
    (400.0, 480.0),
)


# ---------------------------------------------------------------------
# HTTP and source fetchers
# ---------------------------------------------------------------------


def _http_get(url: str) -> bytes:
    """
    Fetch "url" over HTTPS and return the response body as bytes.

    Always sends the module-level "_HTTP_USER_AGENT" so the upstream
    servers don't reject the request with HTTP 403, and applies the
    module-level timeout so a hanging server does not block the run.

    Parameters
    ----------
    url : str
        Absolute URL to GET.

    Returns
    -------
    bytes
        Raw response body. Decoding is left to the caller because some
        sources (CSV) decode straight to text while others (JSON) feed
        the bytes through "json.loads".

    Raises
    ------
    urllib.error.URLError
        If the network call fails (DNS error, connection refused,
        timeout, non-2xx response, ...).
    """
    # Build an explicit Request so we can attach a custom User-Agent
    # header — urllib.request.urlopen() with a bare URL would send the
    # default "Python-urllib/X.Y" UA that both upstreams 403 on.
    request = urllib.request.Request(url, headers={"User-Agent": _HTTP_USER_AGENT})
    with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
        return response.read()


def _save_snapshot(filename: str, raw: bytes) -> None:
    """
    Persist a per-source snapshot to "_CHIRP_FILES_DIR/filename".

    Snapshots are written verbatim, so a "git diff" in GitHub Desktop (or
    any other tool) surfaces exactly what each upstream returned this
    run, byte-for-byte. The destination directory is created on demand.

    Parameters
    ----------
    filename : str
        Basename inside "_CHIRP_FILES_DIR". Existing content is
         overwritten, so each run produces a clean snapshot.
    raw : bytes
        Bytes to write. Callers decide the encoding/format (e.g. CSV
        bytes verbatim, or CHIRP CSV text encoded as UTF-8).

    Returns
    -------
    None
    """
    os.makedirs(_CHIRP_FILES_DIR, exist_ok=True)
    path = os.path.join(_CHIRP_FILES_DIR, filename)
    with open(path, "wb") as f:
        f.write(raw)
    print(f"[..] Saved snapshot: {path}")


def _fetch_chirp_csv(url: str, snapshot_filename: str | None = None) -> tuple[list[Row], list[str]]:
    """
    Download a CHIRP-format CSV from "url" and return "(rows, fieldnames)".

    The source CSV is assumed to already match the CHIRP schema (header
    row plus one row per channel), so we just parse it with
    "csv.DictReader" and forward the result. When "snapshot_filename"
    is provided, the raw download is also written verbatim into
    "_CHIRP_FILES_DIR" before parsing.

    Parameters
    ----------
    url : str
        Absolute URL of a CHIRP-format CSV export.
    snapshot_filename : str | None
        If set, the basename under "_CHIRP_FILES_DIR" to write the raw
        download to. Skipped when "None".

    Returns
    -------
    rows : list[Row]
        Parsed rows, in the order they appeared in the CSV.
    fieldnames : list[str]
        Column names from the CSV header, in the order they appeared.
    """
    # Keep the raw bytes around so the snapshot writer sees exactly what
    # the server returned, byte-for-byte — this preserves upstream
    # quirks (BOMs, trailing whitespace, line endings) in the git diff.
    raw = _http_get(url)
    if snapshot_filename is not None:
        _save_snapshot(snapshot_filename, raw)
    # Decode the response body at once and wrap it in a StringIO so the csv
    # module can consume it as a text stream without touching the disk.
    text = raw.decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = list(reader.fieldnames or [])
    rows = list(reader)
    print(f"[..] Fetched {len(rows)} rows from {url}")
    return rows, fieldnames


def _radioamador_repeater_to_row(entry: dict) -> Row | None:
    """
    Map a single radioamador.info API entry to a CHIRP row.

    Returns "None" for entries that cannot be programmed onto an
    analogue radio (non-FM modes, missing primary frequency, no CTCSS
    tone). The downstream pipeline would drop tone-fewer rows anyway,
    but filtering here keeps the merge step cheap and the row count
    realistic in the per-source log line.

    Parameters
    ----------
    entry : dict
        One element from the "docs" array of the radioamador.info
        "/api/repeaters" response.

    Returns
    -------
    Row | None
        A CHIRP row dict ready to be merged, or "None" if the entry
        should be skipped.
    """
    # Drop anything aren't tagged as an FM-capable node. The
    # radioamador.info dataset also contains DMR/DSTAR/C4FM/TETRA-only
    # entries which we can't program onto an analogue radio.
    if "FM" not in (entry.get("modes") or []):
        return None

    # A repeater may publish several frequency rows (e.g. cross-band
    # operation). The first one flagged "isPrimary" is the canonical
    # input/output pair — anything else is auxiliary and ignored.
    # The explicit annotation lets the type checker narrow "primary"
    # from "dict | None" to "dict" after the None guard below.
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

    # Parse the input/output frequencies as floats so we can compute
    # the duplex direction and offset. Any malformed value disqualifies
    # the entry — we'd rather skip it than emit a half-valid row.
    try:
        out_f = float(primary["outputFrequency"])
        in_f = float(primary["inputFrequency"])
    except (KeyError, TypeError, ValueError):
        return None

    # CTCSS tone gate: no tone means the row would be filtered out
    # downstream anyway, so we bail early. The "0" check is paranoia:
    # the API uses null for "no tone", but defensive parsing is inexpensive.
    # "if not tone" catches None, 0, and "" in a single expression and
    # narrows "tone" to a truthy non-None value for the float() call below.
    tone = primary.get("tone")
    if not tone:
        return None
    try:
        # CHIRP expects tones formatted like "88.5" or "123.0"; coerce
        # whatever the API gave us (int or float) into that shape. The
        # cast just placates the type checker, which won't narrow the
        # "Any | None" returned by dict.get() through a truthy guard.
        tone_str = f"{float(cast(float, tone)):.1f}"
    except (TypeError, ValueError):
        return None

    # Derive the CHIRP-style duplex direction from the input/output
    # frequencies. Equal frequencies mean simplex (no offset).
    if out_f > in_f:
        duplex = "-"
    elif out_f < in_f:
        duplex = "+"
    else:
        duplex = ""
    offset = abs(out_f - in_f)

    # Synthesize a short human-readable comment from the QTH locator
    # and association abbreviation, both of which are useful when
    # browsing the merged list in CHIRP. Either field may be absent.
    comment_bits: list[str] = []
    qth = entry.get("qth_locator")
    if qth:
        comment_bits.append(str(qth))
    association = entry.get("association") or {}
    abbreviation = association.get("abbreviation")
    if abbreviation:
        comment_bits.append(str(abbreviation))

    # The "Location" column is intentionally left out here — the
    # pipeline assigns it after sort/dedup via _renumber_locations.
    # The explicit "Row" annotation forces the type checker to treat
    # the literal as a "dict[str, str]" rather than collapsing it to
    # "Any" because of the "entry.get()" calls inside.
    row: Row = {
        "Name": str(entry.get("callsign", "")).strip(),
        "Frequency": f"{out_f:.4f}",
        "Duplex": duplex,
        "Offset": f"{offset:.6f}",
        "Tone": "Tone",
        "rToneFreq": tone_str,
        "cToneFreq": tone_str,
        "DtcsCode": "023",
        "DtcsPolarity": "NN",
        "Mode": "FM",
        "TStep": "12.50",
        "Skip": "",
        "Comment": " - ".join(comment_bits),
        "URCALL": "",
        "RPT1CALL": "",
        "RPT2CALL": "",
        "DVCODE": "",
    }
    return row


def _fetch_radioamador_json(url: str, snapshot_filename: str | None = None) -> tuple[list[Row], list[str]]:
    """
    Download the radioamador.info JSON and adapt it into CHIRP rows.

    When "snapshot_filename" is provided, the adapted rows are also
    written to "_CHIRP_FILES_DIR" as a CHIRP-format CSV, so the snapshot
    shares a schema with the other two sources (which are raw CSV
    downloads). This keeps cross-source diffs apples-to-apples in
    GitHub Desktop.

    Parameters
    ----------
    url : str
        Absolute URL of the radioamador.info "/api/repeaters" endpoint.
        Callers should include "?limit=500" to defeat the default
        per-page limit of 10.
    snapshot_filename : str | None
        If set, the basename under "_CHIRP_FILES_DIR" to write the
        adapted CHIRP CSV to. Skipped when "None".

    Returns
    -------
    rows : list[Row]
        CHIRP rows synthesized from the FM-capable, tone-enabled
        entries in the payload (others are silently skipped).
    fieldnames : list[str]
        A copy of the canonical CHIRP header, since the JSON source has
        no natural column order of its own.
    """
    payload = json.loads(_http_get(url).decode("utf-8"))
    rows: list[Row] = []
    # docs[] is the list of repeaters. We skip any element the adapter
    # rejects (non-FM, no tone, malformed frequencies, ...). The explicit
    # "Row | None" annotation on "row" pins the type for static checkers
    # — "json.loads" returns "Any", which can otherwise infect the
    # adapter call's inferred return at the local-variable level.
    for entry in payload.get("docs", []):
        row: Row | None = _radioamador_repeater_to_row(entry)
        if row is not None:
            rows.append(row)
    print(f"[..] Fetched {len(rows)} FM rows from {url}")

    if snapshot_filename is not None:
        # Render the adapted rows to CSV text in memory, then hand the
        # encoded bytes to "_save_snapshot" so writing uses the same
        # path/codepath as the raw-CSV sources.
        buffer = io.StringIO()
        writer = csv.DictWriter(
            buffer,
            fieldnames=list(_CHIRP_HEADER),
            restval="",
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)
        _save_snapshot(snapshot_filename, buffer.getvalue().encode("utf-8"))

    return rows, list(_CHIRP_HEADER)


def _download_and_merge() -> tuple[list[Row], list[str]]:
    """
    Download every remote source and return "(rows, fieldnames)".

    Sources are fetched in declaration order so that "first occurrence
    wins" deduplication downstream is deterministic across runs. The
    returned fieldnames are the **union** of every source's columns,
    ordered by first appearance, with "_REQUIRED_FIELDS" appended if
    any of them were missing.

    Returns
    -------
    rows : list[Row]
        Concatenated rows from every source, in fetch order.
    fieldnames : list[str]
        Union of every source's columns plus any required fields the
        sources did not provide.
    """
    rows: list[Row] = []
    fieldnames: list[str] = []
    # Track field name membership in a set for O(1) "have we seen this
    # column before?" checks while preserving first-seen order in the
    # parallel list above.
    seen_fields: set[str] = set()

    # (kind, url, snapshot) tuples. "kind" tells the loop which adapter
    # to use; "snapshot" is the basename written into "_CHIRP_FILES_DIR"
    # so each run produces a diffable on-disk copy of the source data.
    # Adding a new source means appending an entry here and (if it
    # isn't already a CHIRP-format CSV) writing a matching adapter.
    sources: tuple[tuple[str, str, str], ...] = (
        ("csv", _REPETIDORES_PT_URL, _REPETIDORES_PT_SNAPSHOT),
        ("csv", _PORTALDORADIOAMADOR_PT_URL, _PORTALDORADIOAMADOR_PT_SNAPSHOT),
        ("json", _RADIOAMADOR_INFO_URL, _RADIOAMADOR_INFO_SNAPSHOT),
    )

    for kind, url, snapshot in sources:
        if kind == "csv":
            src_rows, src_fields = _fetch_chirp_csv(url, snapshot)
        elif kind == "json":
            src_rows, src_fields = _fetch_radioamador_json(url, snapshot)
        else:
            # Defensive guard for the day someone adds a new "kind"
            # to the source tuple without wiring up the dispatch.
            raise ValueError(f"Unknown source kind: {kind!r}")

        # Append any columns we haven't seen yet, in the order this
        # source listed them. This keeps the merged header stable and
        # predictable run-to-run.
        for name in src_fields:
            if name not in seen_fields:
                seen_fields.add(name)
                fieldnames.append(name)
        rows.extend(src_rows)

    # Make sure every column the pipeline writes to ends up in the
    # output header, even if no source provided it.
    for required in _REQUIRED_FIELDS:
        if required not in seen_fields:
            seen_fields.add(required)
            fieldnames.append(required)

    return rows, fieldnames


# ---------------------------------------------------------------------
# Per-row normalization
# ---------------------------------------------------------------------


def _normalize_row(row: Row) -> Row | None:
    """
    Apply tone filtering and field normalization to a single row.

    Mutates "row" in place and returns it when the row should be kept.
    Returns "None" when the row should be dropped (currently only when
    it lacks a CTCSS tone).

    Parameters
    ----------
    row : Row
        A CHIRP row dict; will be mutated in place if kept.

    Returns
    -------
    Row | None
        The (mutated) row to keep, or "None" to drop it.
    """
    # Skip rows without a CTCSS tone — analogue repeaters without a tone
    # are not part of this workflow.
    if row.get("Tone", "").strip() == "":
        return None

    # If cToneFreq is set, mirror it into rToneFreq so both fields always
    # agree (CHIRP can otherwise reject mismatched pairs).
    ctone = row.get("cToneFreq", "").strip()
    if ctone:
        row["rToneFreq"] = ctone

    # Force the literal "Tone" mode regardless of the source value.
    row["Tone"] = "Tone"

    # Defensive numeric defaults: CHIRP refuses to parse rows where these
    # float fields are blank, so fall back to a safe value.
    if not row.get("rToneFreq", "").strip():
        row["rToneFreq"] = _DEFAULT_TONE_FREQ
    if not row.get("cToneFreq", "").strip():
        row["cToneFreq"] = _DEFAULT_TONE_FREQ
    if not row.get("TStep", "").strip():
        row["TStep"] = _DEFAULT_TSTEP

    # Clear DVCODE — this script only targets analogue (FM) repeaters.
    row["DVCODE"] = ""

    return row


def _normalize_rows(rows: list[Row]) -> list[Row]:
    """
    Apply "_normalize_row" to every row, dropping filtered ones.

    Parameters
    ----------
    rows : list[Row]
        Rows to normalize. Surviving rows are mutated in place.

    Returns
    -------
    list[Row]
        The rows that "_normalize_row" chose to keep, in the same
        relative order as the input.
    """
    normalized: list[Row] = []
    for row in rows:
        # Explicit "Row | None" annotation pins the type for the static
        # checker, mirroring the same pattern used in
        # "_fetch_radioamador_json" — without it the local variable is
        # inferred as "Any | None" and the "append" below complains.
        kept: Row | None = _normalize_row(row)
        if kept is not None:
            normalized.append(kept)
    return normalized


# ---------------------------------------------------------------------
# Frequency helpers, band filter, dedup, sort, renumber
# ---------------------------------------------------------------------


def _row_frequency(row: Row) -> float:
    """
    Return "Frequency" as a float, falling back to 0.0 on bad input.

    Parameters
    ----------
    row : Row
        Any CHIRP row.

    Returns
    -------
    float
        Parsed frequency in MHz, or "0.0" if the column is missing,
        empty, or unparseable. 0.0 is outside every supported band and
        sorts to the top, so a malformed row remains visible rather
        than silently disappearing.
    """
    try:
        return float(row.get("Frequency", "0") or 0)
    except ValueError:
        # Malformed Frequency — fall back to 0.0 so the row still
        # participates in dedup/sort rather than crashing the run.
        return 0.0


def _is_supported_frequency(freq: float) -> bool:
    """
    Return "True" if "freq" (MHz) falls inside any supported band.

    Parameters
    ----------
    freq : float
        Frequency in MHz.

    Returns
    -------
    bool
        "True" iff "freq" falls within one of
        "_SUPPORTED_FREQ_RANGES".
    """
    return any(low <= freq <= high for low, high in _SUPPORTED_FREQ_RANGES)


def _filter_by_supported_frequency(rows: list[Row]) -> list[Row]:
    """
    Drop rows whose "Frequency" lies outside every supported band.

    The radio (and CHIRP) will refuse to write a row whose frequency
    does not fall within one of "_SUPPORTED_FREQ_RANGES", so we strip
    them here rather than letting the import fail later.

    Parameters
    ----------
    rows : list[Row]
        Rows to filter.

    Returns
    -------
    list[Row]
        Only the rows whose frequency falls inside a supported band,
        in their original relative order.
    """
    kept: list[Row] = []
    dropped = 0
    for row in rows:
        if _is_supported_frequency(_row_frequency(row)):
            kept.append(row)
        else:
            dropped += 1
    if dropped:
        # Surface the actual band list in the log so the user can see
        # at a glance which ranges are being enforced.
        bands = ", ".join(f"{low}-{high}MHz" for low, high in _SUPPORTED_FREQ_RANGES)
        print(f"[..] Removed {dropped} row(s) outside supported ranges {bands}")
    return kept


def _dedupe_by_name_frequency(rows: list[Row]) -> list[Row]:
    """
    Deduplicate rows by "(Name, float(Frequency))", first occurrence wins.

    Frequency is normalized to a float for the dedup key so that values
    like "51.9900" and "51.990000" — which differ as strings but
    match as numbers — are treated as the same entry.

    Parameters
    ----------
    rows : list[Row]
        Rows to deduplicate.

    Returns
    -------
    list[Row]
        The kept rows in their original relative order. Because earlier
        sources are processed first in "_download_and_merge", this
        effectively means the first source's row wins when several
        sources publish the same channel.
    """
    seen: set[tuple[str, float]] = set()
    deduped: list[Row] = []
    duplicates = 0
    for row in rows:
        key = (row.get("Name", "").strip(), _row_frequency(row))
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        deduped.append(row)
    if duplicates:
        print(f"[..] Removed {duplicates} duplicate row(s) by (Name, Frequency)")
    return deduped


def _sort_by_frequency(rows: list[Row]) -> None:
    """
    Sort rows in-place by "Frequency" ascending.

    Parameters
    ----------
    rows : list[Row]
        Rows to sort. Modified in place.

    Returns
    -------
    None
    """
    rows.sort(key=_row_frequency)


def _renumber_locations(rows: list[Row]) -> None:
    """
    Rewrite the "Location" column as a contiguous 0-based index.

    The source files each start their own "Location" at 1, so a naive
    concatenation produces collisions; we rewrite the column here so
    the merged CSV reads cleanly as "0, 1, 2, ...".

    Parameters
    ----------
    rows : list[Row]
        Rows to renumber, already in their final (sorted) order.
        Modified in place.

    Returns
    -------
    None
    """
    for i, row in enumerate(rows):
        row["Location"] = str(i)


# ---------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------


def _write_csv(output_file: str, fieldnames: list[str], rows: list[Row]) -> None:
    """
    Write "rows" to "output_file" using "fieldnames" as the header.

    Uses 'restval=""' so rows missing a column are written with an
    empty string for that column, and 'extrasaction="ignore"' so any
    stray keys not in "fieldnames" are silently dropped instead of
    raising.

    Parameters
    ----------
    output_file : str
        Destination path for the merged CSV. Overwritten if it exists.
    fieldnames : list[str]
        Column order to write — also the header row.
    rows : list[Row]
        Rows to write, in the order given.

    Returns
    -------
    None
    """
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, restval="", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------
# Public orchestrator + entry point
# ---------------------------------------------------------------------


def clean_chirp_csvs(output_file: str) -> None:
    """
    Download every remote repeater source, merge and normalize the rows,
    and write the cleaned CHIRP CSV to "output_file".

    The pipeline applies the following transformations, in order:

    1. **Download** — every remote source (two CHIRP-format CSV exports
       and the radioamador.info JSON API) is fetched in memory; nothing
       is written to disk except the final merged CSV. The output
       header is the **union** of every source's fieldnames, preserving
       the order of the first source and appending any new columns as
       they appear in later sources. Rows missing a column are written
       with an empty string for that column, and any stray keys not in
       the merged header are ignored when writing.
    2. **Filtering** — rows without a "Tone" value are dropped, since
       analogue repeater entries without a CTCSS tone are not useful
       for this workflow.
    3. **Tone normalization** — when "cToneFreq" is present, it is
       copied into "rToneFreq" so both fields stay consistent, and
       "Tone" is forced to the literal string "Tone".
    4. **Defensive defaults** — any empty "rToneFreq" / "cToneFreq" /
       "TStep" field is filled with a safe default, because CHIRP
       refuses to parse rows where these numeric columns are blank.
    5. **DVCODE reset** — the digital-mode field is cleared, since this
       workflow targets analogue (FM) repeaters only.
    6. **Frequency-range filter** — rows whose "Frequency" falls
       outside the supported radio bands (65-108 MHz, 136-174 MHz,
       400-480 MHz) are dropped, since CHIRP and the target radio
       reject them with "Frequency X is out of supported ranges ..."
       errors.
    7. **Deduplication** — rows are deduplicated by "(Name, Frequency)",
       comparing "Frequency" as a float so values like "51.9900" and
       "51.990000" collapse into one entry. The first occurrence wins.
    8. **Sorting** — surviving rows are sorted by "Frequency" ascending.
    9. **Renumbering** — the "Location" column is rewritten as a
       0-based sequence reflecting the final sorted order.

    Parameters
    ----------
    output_file : str
        Destination path for the merged, cleaned CSV. Overwritten if
        it exists.

    Raises
    ------
    ValueError
        If, after filtering, no tone-enabled rows remain to write.
    urllib.error.URLError
        If any remote source is unreachable or returns a non-2xx
        response.

    Returns
    -------
    None
    """
    # 1. Download every source and merge them into a single row list
    #    with a union header.
    rows, fieldnames = _download_and_merge()

    # 2-5. Tone filter + field normalization + DVCODE reset.
    rows = _normalize_rows(rows)
    # 6. Band filter — drop rows outside the radio's supported ranges.
    rows = _filter_by_supported_frequency(rows)
    # 7. Deduplicate by (Name, float(Frequency)); first occurrence wins.
    rows = _dedupe_by_name_frequency(rows)
    # 8. Sort by frequency ascending so the final CSV reads in order.
    _sort_by_frequency(rows)
    # 9. Renumber Location to a contiguous 0-based index now that
    #    dedup/sort have reshuffled the rows.
    _renumber_locations(rows)

    # If every row was filtered out, we deliberately do NOT overwrite an
    # existing output file with an empty one — the user is better
    # served by a loud failure than a silently clobbered CSV.
    if not rows:
        raise ValueError("No valid tone-enabled rows found. Output file not generated.")

    _write_csv(output_file, fieldnames, rows)
    print(f"[OK] Merged {len(rows)} rows into {output_file}")


def main() -> None:
    """
    Default entry point: download every source and merge into "chirp.csv".

    Importable callers should call "clean_chirp_csvs" directly with
    a custom "output_file" instead of going through this wrapper.
    """
    clean_chirp_csvs(output_file="chirp.csv")


if __name__ == "__main__":
    # Run as a script: `python3 HAM_Repeaters_CHIRP.py`.
    main()
