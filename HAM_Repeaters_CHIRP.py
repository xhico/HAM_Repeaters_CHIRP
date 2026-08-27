"""
HAM_Repeaters_CHIRP
===================

Build a CHIRP-compatible CSV from ANACOM's license registry — the
Portuguese regulator's own record of every authorized amateur repeater,
and the source the community directories ultimately derive from.

ANACOM publishes no API. The data lives behind a session-based search
form, and the CTCSS tone is not in the results table at all: it sits on
each station's detail page. So a run is:

    1. GET  pesquisa-eucla.do    open a session, read its jsessionid
    2. POST resultados-eucla.do  every station, one row each
    3. POST detalhes-eucla.do    once per repeater, for the tone

That last step is one request per repeater, so a full run takes a couple
of minutes and issues ~140 requests. It is meant to be run occasionally
(monthly is plenty — licenses do not change often), not on a schedule.

Rows are then filtered to tone-enabled analogue repeaters inside the
radio's supported bands, deduplicated, sorted by frequency and numbered.
"""

# Standard library only, so this runs on a stock Python 3.10+ interpreter.
import csv
import html
import http.client
import http.cookiejar
import re
import sys
import time
import urllib.parse
import urllib.request
from typing import TypeAlias

# A CHIRP row. CHIRP CSVs are text-only, so every column is a string —
# even numeric ones ("145.6000", "0.600000").
Row: TypeAlias = dict[str, str]

# ANACOM replaced its static station lists with this search app. "eucla"
# is the shared-use-station ("Estações de Uso Comum") category, which is
# what amateur repeaters are licensed under.
_ANACOM_FORM_URL = "https://www.anacom.pt/saas/pesquisa-eucla.do"
_ANACOM_RESULTS_URL = "https://www.anacom.pt/saas/resultados-eucla.do"
_ANACOM_DETAILS_URL = "https://www.anacom.pt/saas/detalhes-eucla.do"

# The search form's "select everything" flags. Submitted unticked, the
# form returns the page chrome with no result table at all.
_SEARCH_ALL_FIELDS = {
    "justSubmited": "true",
    "formBean.allTerritories": "true",
    "formBean.allIndicativos": "true",
    "formBean.allAssociacoes": "true",
    "formBean.allFrequencias": "true",
}

# ANACOM rejects the default "Python-urllib/X.Y" User-Agent.
_HTTP_USER_AGENT = "Mozilla/5.0 (HAM_Repeaters_CHIRP)"
_HTTP_TIMEOUT_SECONDS = 60

# Pause between detail requests. One run is ~140 of them against a
# government site that owes us nothing; this keeps the load courteous.
_REQUEST_DELAY_SECONDS = 0.4

# ANACOM's label for a conventional analogue voice repeater. Its other
# categories here are beacons and stations with no assigned frequencies,
# neither of which belongs in a CHIRP channel list.
_ANALOGUE_REPEATER = "estação repetidora de modulação analógica"

# If the scrape returns fewer rows than this, assume the form or the
# markup changed rather than believing the registry emptied out — better
# to fail loudly than to overwrite a good CSV with a handful of rows.
_MIN_PLAUSIBLE_ROWS = 50

# Result-table column positions, confirmed against the live output:
#   0 holder  1 territory  2 station type  3 callsign
#   4 band    5 emission (repeater output)  6 reception (repeater input)
_COL_HOLDER, _COL_TYPE, _COL_CALLSIGN = 0, 2, 3
_COL_EMISSION, _COL_RECEPTION = 5, 6
_MIN_COLUMNS = 7

# Portuguese amateur callsigns, used to tell data rows from the layout
# rows the results table is padded with.
_CALLSIGN_RE = re.compile(r"C[QRTS][0-9][A-Z]{1,4}")

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

# 12.50 kHz matches the Portuguese narrowband channel plan. CHIRP refuses
# to parse a blank TStep.
_DEFAULT_TSTEP = "12.50"

# Bands the radio accepts, MHz, inclusive. Anything else is dropped, since
# CHIRP rejects it with "Frequency X is out of supported ranges ...".
_SUPPORTED_FREQ_RANGES: tuple[tuple[float, float], ...] = (
    (65.0, 108.0),
    (136.0, 174.0),
    (400.0, 480.0),
)


# ---------------------------------------------------------------------
# ANACOM scraping
# ---------------------------------------------------------------------


def _strip_markup(fragment: str) -> str:
    """Reduce an HTML fragment to its collapsed, unescaped text."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _open_session() -> tuple[urllib.request.OpenerDirector, str]:
    """
    Open a search session and return "(opener, jsessionid)".

    The session id is carried in the search form's action URL as well as
    in the cookie; posting without it returns the empty search page.
    """
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    print(f"[..] Opening {_ANACOM_FORM_URL}")
    request = urllib.request.Request(_ANACOM_FORM_URL, headers={"User-Agent": _HTTP_USER_AGENT})
    with opener.open(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
        form = response.read().decode("utf-8", "replace")

    match = re.search(r'action="resultados-[a-z]+\.do;jsessionid=([a-zA-Z0-9]+)"', form)
    if match is None:
        raise RuntimeError(
            "No jsessionid in the ANACOM search form — the page layout has changed."
        )
    return opener, match.group(1)


def _post(opener: urllib.request.OpenerDirector, url: str, fields: dict[str, str]) -> str:
    """POST "fields" to "url" through "opener" and return the response text."""
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(fields).encode(),
        headers={"User-Agent": _HTTP_USER_AGENT},
    )
    with opener.open(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8", "replace")


def _parse_result_rows(page: str) -> list[dict]:
    """
    Extract the analogue repeaters from the results table.

    Each entry carries the summary columns plus "eucla_id", the token the
    detail page is fetched with.
    """
    records: list[dict] = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        raw_cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        cells = [_strip_markup(c) for c in raw_cells]
        if len(cells) < _MIN_COLUMNS or not _CALLSIGN_RE.fullmatch(cells[_COL_CALLSIGN]):
            continue
        if _ANALOGUE_REPEATER not in cells[_COL_TYPE].lower():
            continue
        identifier = re.search(r'name="detailsBean\.euclaId"[^>]*value="([^"]+)"', tr)
        if identifier is None:
            continue
        records.append(
            {
                "callsign": cells[_COL_CALLSIGN],
                "holder": cells[_COL_HOLDER],
                "emission": _parse_frequency(cells[_COL_EMISSION]),
                "reception": _parse_frequency(cells[_COL_RECEPTION]),
                "eucla_id": identifier.group(1),
            }
        )
    return records


def _parse_frequency(cell: str) -> float | None:
    """
    Parse the first "439,1000 MHz" in a table cell into MHz.

    Returns None when the cell holds no frequency — "-" is used for fields
    that do not apply. Numbers are Portuguese-formatted: "." groups
    thousands, "," is the decimal separator.
    """
    match = re.search(r"([\d.,]+)\s*MHz", cell)
    if match is None:
        return None
    try:
        return float(match.group(1).replace(".", "").replace(",", "."))
    except ValueError:
        return None


def _fetch_detail(opener: urllib.request.OpenerDirector, eucla_id: str) -> dict[str, str]:
    """
    Fetch one station's detail page and return its labeled fields.

    The page renders them as "<h1>label</h1><span>value</span>" pairs. HTML
    comments are stripped first: the markup keeps a commented-out
    "Frequência de Emissão" block that a label search would otherwise pick
    up in place of the real value.
    """
    page = _post(opener, _ANACOM_DETAILS_URL, {"detailsBean.euclaId": eucla_id})
    page = re.sub(r"<!--.*?-->", "", page, flags=re.S)

    fields: dict[str, str] = {}
    for label, body in re.findall(r"<h1>(.*?)</h1>(.*?)</li>", page, re.S):
        # Take the value out of its <span>, not the whole list item: the
        # "Local da Estação" entry also holds a "ver mapa" button whose
        # label would otherwise be glued onto the locator.
        span = re.search(r"<span[^>]*>(.*?)</span>", body, re.S)
        fields[_strip_markup(label).lower()] = _strip_markup(span.group(1) if span else body)
    return fields


# ---------------------------------------------------------------------
# CHIRP row construction
# ---------------------------------------------------------------------


def _to_chirp_row(record: dict, detail: dict[str, str]) -> Row | None:
    """
    Combine a results row and its detail page into a CHIRP row.

    Returns None when the station cannot be programmed as an analogue
    channel: no licensed frequency pair, or no CTCSS tone. ANACOM does not
    publish the channel bandwidth in this view, so Mode is always FM.
    """
    # The repeater's emission is what the radio receives, its reception
    # what the radio transmits.
    out_f = record["emission"]
    in_f = record["reception"]
    if out_f is None or in_f is None:
        return None

    # "Tom de proteção" reads like "123 Hz" or "131,8 Hz"; stations without
    # one show "-".
    tone_match = re.match(r"([\d,]+)", detail.get("tom de proteção", ""))
    if tone_match is None:
        return None
    try:
        tone_value = float(tone_match.group(1).replace(",", "."))
    except ValueError:
        return None
    if tone_value <= 0:
        return None

    if out_f > in_f:
        duplex = "-"
    elif out_f < in_f:
        duplex = "+"
    else:
        duplex = ""

    # "Local da Estação" holds the QTH locator. Channel designator and
    # license holder round out something readable in CHIRP's comment column.
    comment = " - ".join(
        bit for bit in (
            detail.get("local da estação", ""),
            detail.get("canal", ""),
            record["holder"],
        ) if bit and bit != "-"
    )

    return {
        "Location": "",
        "Name": record["callsign"],
        "Frequency": f"{out_f:.4f}",
        "Duplex": duplex,
        "Offset": f"{abs(out_f - in_f):.6f}",
        "Tone": "Tone",
        "rToneFreq": f"{tone_value:.1f}",
        "cToneFreq": f"{tone_value:.1f}",
        "DtcsCode": "023",
        "DtcsPolarity": "NN",
        "Mode": "FM",
        "TStep": _DEFAULT_TSTEP,
        "Skip": "",
        "Comment": comment,
        "URCALL": "",
        "RPT1CALL": "",
        "RPT2CALL": "",
        "DVCODE": "",
    }


def _download_repeaters() -> list[Row]:
    """
    Scrape ANACOM and return every programmable repeater as a CHIRP row.

    Raises RuntimeError if the search returns implausibly few rows, rather
    than reporting an empty registry and overwriting a good CSV.
    """
    opener, session_id = _open_session()

    print("[..] Submitting the search")
    page = _post(
        opener,
        f"{_ANACOM_RESULTS_URL};jsessionid={session_id}",
        _SEARCH_ALL_FIELDS,
    )
    records = _parse_result_rows(page)
    if len(records) < _MIN_PLAUSIBLE_ROWS:
        raise RuntimeError(
            f"ANACOM returned only {len(records)} analogue repeaters (expected at least "
            f"{_MIN_PLAUSIBLE_ROWS}). Treating this as a broken scrape, not an empty "
            "registry. Output file not generated."
        )
    print(f"[..] Found {len(records)} licensed analogue repeaters")

    rows: list[Row] = []
    skipped = 0
    for index, record in enumerate(records, start=1):
        # One request per station: the tone lives only on the detail page.
        detail = _fetch_detail(opener, record["eucla_id"])
        row = _to_chirp_row(record, detail)
        if row is None:
            skipped += 1
        else:
            rows.append(row)
        if index % 25 == 0 or index == len(records):
            print(f"[..] Fetched details {index}/{len(records)}")
        time.sleep(_REQUEST_DELAY_SECONDS)

    if skipped:
        print(f"[..] Skipped {skipped} station(s) with no CTCSS tone or no frequency pair")
    return rows


# ---------------------------------------------------------------------
# Filtering, dedup, output
# ---------------------------------------------------------------------


def _row_frequency(row: Row) -> float:
    """Return "Frequency" as a float, or 0.0 if missing or malformed."""
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
    """Deduplicate by "(Name, float(Frequency))", first occurrence wins."""
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
    """Rewrite "Location" as a contiguous index starting at 1, in place."""
    for index, row in enumerate(rows, start=1):
        row["Location"] = str(index)


def _write_csv(output_file: str, rows: list[Row]) -> None:
    """Write "rows" to "output_file", overwriting it."""
    # newline="" stops the text layer translating line endings, so the
    # writer's own lineterminator is what reaches disk.
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=list(_CHIRP_HEADER),
            restval="",
            extrasaction="ignore",
            lineterminator=_CSV_LINE_TERMINATOR,
        )
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------
# Orchestrator + entry point
# ---------------------------------------------------------------------


def build_chirp_csv(output_file: str) -> None:
    """
    Scrape ANACOM, build the channel list and write it to "output_file".

    Raises ValueError if nothing survives filtering — better than
    clobbering an existing CSV with an empty one.
    """
    rows = _download_repeaters()
    rows = _filter_by_supported_frequency(rows)
    rows = _dedupe_by_name_frequency(rows)
    rows.sort(key=_row_frequency)
    _renumber_locations(rows)

    if not rows:
        raise ValueError("No valid tone-enabled rows found. Output file not generated.")

    _write_csv(output_file, rows)
    print(f"[OK] Wrote {len(rows)} rows to {output_file}")


def main() -> None:
    """Entry point: build "chirp.csv" from the ANACOM registry."""
    try:
        build_chirp_csv(output_file="chirp.csv")
    except (OSError, RuntimeError, http.client.HTTPException) as exc:
        # ANACOM is scraped, not consumed through an API: an unreachable
        # host or a reshaped page is expected wear, not a crash worth a
        # traceback.
        print(f"[!!] Could not build the channel list: {type(exc).__name__}: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
