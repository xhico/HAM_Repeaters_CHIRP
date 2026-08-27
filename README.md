# **HAM Repeaters** CHIRP

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)

A small Python utility that builds a [CHIRP](https://chirpmyradio.com/)
CSV of Portuguese amateur repeaters straight from
[ANACOM](https://www.anacom.pt/)'s license registry — the national
regulator's own record of every authorized station, and the source the
community repeater directories ultimately derive from.

The result is a single channel list with consistent CTCSS tones, no
duplicates, sorted by frequency, ready to import into CHIRP and write to
an analogue radio.

---

## Source

Everything comes from one place: ANACOM's *Serviço Amador e Amador
Satélite* search, under the **EUC** (*Estações de Uso Comum*) category
that amateur repeaters are licensed under.

ANACOM publishes no API. The data sits behind a session-based search
form, and — importantly — **the CTCSS tone is not in the results table at
all**. It lives on each station's individual detail page. So a run makes
three kinds of request:

| Step | Request | Purpose |
|------|---------|---------|
| 1 | `GET pesquisa-eucla.do` | Open a session, read its `jsessionid` |
| 2 | `POST resultados-eucla.do` | Every licensed station, one row each |
| 3 | `POST detalhes-eucla.do` | Once **per repeater** — the tone, channel and locator |

That third step is one request per repeater, so a full run issues around
140 requests and takes roughly **90 seconds to two minutes**, with a
deliberate short pause between them to stay courteous to a government
site. It is designed to be run occasionally — monthly is plenty, since
licenses change slowly — not on a schedule.

Only stations typed `Estação repetidora de modulação analógica` are used.
ANACOM's other categories in this search are beacons and stations with no
assigned frequencies, neither of which belongs in a CHIRP channel list.

### What this does and does not give you

ANACOM is authoritative about what is **licensed**. It cannot tell you
what is actually **on the air** — a license may cover a repeater that is
switched off, awaiting installation, or operating differently from its
paperwork. It also does not publish the channel bandwidth, so every row
is written as `Mode=FM`.

If a repeater in the list turns out to be silent, that is the registry
being a registry, not a bug in this script.

---

## Features

- **Scrape** — walks ANACOM's search form and per-station detail pages,
  in memory; nothing is written to disk except the final CSV.
- **Adapt** — licensed emission/reception frequencies become
  `Frequency`/`Duplex`/`Offset`, and `Tom de proteção` becomes
  `rToneFreq`/`cToneFreq`.
- **Filter** — drops stations with no CTCSS tone or no licensed
  frequency pair, since neither can be programmed as a usable channel.
- **Band filter** — drops frequencies outside the supported radio bands
  (`65-108 MHz`, `136-174 MHz`, `400-480 MHz`), so CHIRP and the radio do
  not reject them with "out of supported ranges" errors.
- **Deduplicate** — by `(Name, Frequency)`, comparing the frequency as a
  float so `51.9900` and `51.990000` collapse into one entry.
- **Sort** — by `Frequency` ascending.
- **Renumber** — rewrites `Location` as a contiguous sequence starting at 1.
- **Refuses to guess** — if the scrape returns implausibly few rows, the
  script fails instead of overwriting a good CSV with a broken one.

---

## Requirements

- Python **3.10+** (uses builtin generics like `list[str]` / `set[tuple[...]]`
  and the PEP 604 `X | None` union syntax)
- No third-party dependencies — only the Python standard library
  (`csv`, `html`, `http.client`, `http.cookiejar`, `re`, `urllib`)
- Outbound HTTPS access to `www.anacom.pt`.

---

## Installation

Clone the repository and (optionally) create a virtual environment:

```bash
git clone <repo-url> HAM_Repeaters_CHIRP
cd HAM_Repeaters_CHIRP

# optional but recommended
python3 -m venv .venv
source .venv/bin/activate
```

There is nothing to `pip install` — the script runs as-is on a stock
Python 3.10+ interpreter.

---

## Project layout

```
HAM_Repeaters_CHIRP/
├── HAM_Repeaters_CHIRP.py   # the scrape + build script
└── chirp.csv                # generated output (after running the script)
```

The output always uses the canonical CHIRP header:
`Location,Name,Frequency,Duplex,Offset,Tone,rToneFreq,cToneFreq,DtcsCode,DtcsPolarity,Mode,TStep,Skip,Comment,URCALL,RPT1CALL,RPT2CALL,DVCODE`.

`Comment` carries the QTH locator, the channel designator and the license
holder, for example:
`HM76KX - RV48 - Associação de Radioamadores Marienses`.

---

## Usage

From the project root, run:

```bash
python3 HAM_Repeaters_CHIRP.py
```

`chirp.csv` is written to the project root. Import it into CHIRP via
**File → Import** and write it to your radio.

Sample output:

```
[..] Opening https://www.anacom.pt/saas/pesquisa-eucla.do
[..] Submitting the search
[..] Found 140 licensed analogue repeaters
[..] Fetched details 25/140
[..] Fetched details 50/140
[..] Fetched details 75/140
[..] Fetched details 100/140
[..] Fetched details 125/140
[..] Fetched details 140/140
[..] Skipped 1 station(s) with no CTCSS tone or no frequency pair
[OK] Wrote 139 rows to chirp.csv
```

### Custom output path

Edit the call inside `main()` at the bottom of the script:

```python
def main() -> None:
    build_chirp_csv(output_file="chirp.csv")
```

Or import it from another script:

```python
from HAM_Repeaters_CHIRP import build_chirp_csv

build_chirp_csv(output_file="/path/to/repeaters.csv")
```

---

## Processing pipeline

| Step | What it does |
|------|--------------|
| 1. Session | Opens the search form, reads the `jsessionid` out of its action URL |
| 2. Search | Submits the "select everything" flags; parses the results table |
| 3. Select | Keeps only `Estação repetidora de modulação analógica` rows |
| 4. Detail | One request per repeater for `Tom de proteção`, `Canal`, locator |
| 5. Adapt | Builds CHIRP rows; drops stations with no tone or no frequency pair |
| 6. Band filter | Drops rows outside `65-108`, `136-174`, `400-480 MHz` |
| 7. Deduplicate | By `(Name, float(Frequency))`, first occurrence wins |
| 8. Sort | By `Frequency` ascending |
| 9. Renumber | Rewrites `Location` as a contiguous index starting at 1 |
| 10. Write | Writes the CSV to `output_file` |

---

## Module API

| Function | Purpose |
|----------|---------|
| `build_chirp_csv(output_file)` | Public entry point — scrape, build, write |
| `main()` | Default `__main__` entry point (writes to `chirp.csv`) |
| `_open_session()` | Open the search session; return `(opener, jsessionid)` |
| `_post(opener, url, fields)` | POST form fields through the session |
| `_parse_result_rows(page)` | Extract the analogue repeaters from the results table |
| `_fetch_detail(opener, eucla_id)` | Fetch one station's detail page as labeled fields |
| `_to_chirp_row(record, detail)` | Combine a results row and its detail into a CHIRP row |
| `_download_repeaters()` | Run the whole scrape; return CHIRP rows |
| `_strip_markup(fragment)` | Reduce an HTML fragment to collapsed text |
| `_parse_frequency(cell)` | Parse a Portuguese-formatted MHz value out of a table cell |
| `_row_frequency(row)` | Parse `Frequency` as `float` (0.0 fallback) |
| `_filter_by_supported_frequency(rows)` | Drop out-of-band rows |
| `_dedupe_by_name_frequency(rows)` | First-occurrence-wins dedup |
| `_renumber_locations(rows)` | Rewrite `Location` as a contiguous index starting at 1 |
| `_write_csv(output_file, rows)` | `DictWriter` with `restval=""`, `extrasaction="ignore"` |

Module-level constants `_ANACOM_FORM_URL`, `_ANACOM_RESULTS_URL`,
`_ANACOM_DETAILS_URL`, `_SEARCH_ALL_FIELDS`, `_HTTP_USER_AGENT`,
`_HTTP_TIMEOUT_SECONDS`, `_REQUEST_DELAY_SECONDS`, `_ANALOGUE_REPEATER`,
`_MIN_PLAUSIBLE_ROWS`, `_CALLSIGN_RE`, `_CHIRP_HEADER`,
`_CSV_LINE_TERMINATOR`, `_DEFAULT_TSTEP` and `_SUPPORTED_FREQ_RANGES`
centralize the endpoints, the search parameters, HTTP settings, the
station type kept, the sanity floor, the canonical CHIRP header, the line
ending written, the default `TStep` and the accepted radio bands.

---

## Errors

- `ValueError: No valid tone-enabled rows found. Output file not
  generated.` — everything was filtered out, so the existing CSV is left
  untouched.
- `[!!] Could not build the channel list: RuntimeError: ANACOM returned
  only N analogue repeaters ...` — the search came back with implausibly
  few rows. This is treated as a broken scrape, not an empty registry,
  and nothing is written.
- `[!!] Could not build the channel list: RuntimeError: No jsessionid in
  the ANACOM search form ...` — the form's markup changed and the session
  could not be opened.
- `[!!] Could not build the channel list: URLError ...` — `www.anacom.pt`
  is unreachable.

Since ANACOM is scraped rather than consumed through an API, its markup
can change without notice. All of the above exit with status 1 and a
single `[!!]` line rather than a traceback.

---

## License

Released under the [MIT License](LICENSE) — © 2026 xhico.

You are free to use, modify, and redistribute this code, including in
commercial projects, as long as the copyright notice and the license
text are preserved. The software is provided "as is", without warranty
of any kind.
