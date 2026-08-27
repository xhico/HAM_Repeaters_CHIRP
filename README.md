# **HAM Repeaters** CHIRP

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)

A small Python utility that downloads repeater lists from public
Portuguese amateur-radio sources and merges them into a single, cleaned,
deduplicated [CHIRP](https://chirpmyradio.com/) CSV ready to be imported
into CHIRP and flashed onto an analogue radio.

The tool is aimed at amateur radio operators who want one consolidated
channel list, sourced from multiple national directories, with
consistent CTCSS tones, no duplicates, and sorted by frequency.

---

## Sources

On every run the script fetches the following feeds over HTTPS and
merges them in memory — nothing is written to disk except the final
output CSV.

| Feed                                                             | Format          | Notes                                                              |
|------------------------------------------------------------------|-----------------|--------------------------------------------------------------------|
| `https://repetidores.pt/json_vhf.php`                            | DataTables JSON | Adapted into CHIRP rows; tone-enabled repeaters only.              |
| `https://repetidores.pt/json_uhf.php`                            | DataTables JSON | Same feed for UHF.                                                 |
| `https://portaldoradioamador.pt/api/v1/repeaters/fact-repeater/` | Django REST JSON | Adapted into CHIRP rows; analogue FM with a CTCSS tone only.       |
| `https://api.radioamador.info/api/repeaters?limit=500`           | PayloadCMS JSON | Adapted into CHIRP rows: FM-mode entries with a CTCSS tone only.   |

Order matters — deduplication is first-occurrence-wins, so a feed listed
earlier takes precedence when several publish the same channel.

Both Portuguese sites also publish ready-made CHIRP CSV exports, but the
APIs their own front-ends use carry more. repetidores.pt's two JSON feeds
add channel bandwidth, QTH locator, ERP and owner over `/gerarcsv.php`.
portaldoradioamador.pt's `fact-repeater` endpoint reports the bandwidth
its CHIRP export flattens to `FM`, and avoids the malformed rows that
export emits for reverse and simplex repeaters (a negative `Offset`, and
a `-` duplex on a simplex channel).

Two categories are deliberately not merged, because neither can be
programmed as an analogue duplex channel: repetidores.pt's
`json_dmr.php` / `json_dstar.php` feeds (digital-only repeaters), and
portaldoradioamador.pt records marked `band: "X"` (cross-band repeaters,
whose input and output are 286-409 MHz apart). The sites' own CHIRP
exports omit both for the same reason.

Every upstream rejects the default `Python-urllib` User-Agent with HTTP
403, so a generic browser-style UA is sent on every request.

### When a source is down

Each feed is fetched independently. One that is unreachable or returns
unusable data is logged with an `[!!]` line and skipped, and the merge
continues with the rest — you lose that feed's unique channels for the
run, not the whole output. The script only fails outright, with
`RuntimeError`, when *every* feed is unusable.

---

## Features

- **Download** — fetches every feed over HTTPS in memory; no files
  written to disk besides the final merged CSV.
- **Adapt** — maps every feed into CHIRP rows: output/input frequency →
  `Frequency`/`Duplex`/`Offset`, CTCSS tone copied into
  `rToneFreq`/`cToneFreq`, and `Mode=NFM` for narrow (12.5 kHz) channels
  against `Mode=FM` for wide ones, from the bandwidth both Portuguese
  APIs report.
- **Filter** — drops rows that do not carry a CTCSS tone, since this
  workflow targets tone-enabled analogue repeaters only.
- **Normalize** — keeps `rToneFreq` and `cToneFreq` consistent, fills
  defensive defaults so CHIRP does not refuse to parse the row, and
  clears `DVCODE` (analogue-only).
- **Band filter** — drops rows whose frequency falls outside the
  supported radio bands (`65-108 MHz`, `136-174 MHz`, `400-480 MHz`),
  so CHIRP/the radio do not reject them with "out of supported ranges"
  errors.
- **Deduplicate** — removes duplicates by `(Name, Frequency)`, comparing
  `Frequency` as a float so `51.9900` and `51.990000` collapse into one
  entry. The first occurrence wins (feeds are queried in the order
  listed above).
- **Sort** — orders the surviving rows by `Frequency` ascending.
- **Renumber** — rewrites the `Location` column as a contiguous 0-based
  sequence reflecting the final order.

---

## Requirements

- Python **3.10+** (uses builtin generics like `list[str]` / `set[tuple[...]]`
  and the PEP 604 `X | None` union syntax)
- No third-party dependencies — only the Python standard library
  (`csv`, `http.client`, `json`, `urllib`)
- Outbound HTTPS access to the source hosts listed above.

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
├── HAM_Repeaters_CHIRP.py   # the download + merge + clean script
└── chirp.csv                # generated output (after running the script)
```

The output header is the **union** of every source's fieldnames,
ordered by first appearance: columns from the first source come first,
and any new columns introduced by later sources are appended in the
order they appear. Rows missing a column are written with an empty
string for that column.

A typical CHIRP header looks like:
`Location,Name,Frequency,Duplex,Offset,Tone,rToneFreq,cToneFreq,DtcsCode,DtcsPolarity,Mode,TStep,Skip,Comment,URCALL,RPT1CALL,RPT2CALL,DVCODE`.

---

## Usage

From the project root, run:

```bash
python3 HAM_Repeaters_CHIRP.py
```

The merged file `chirp.csv` is written to the project
root. Import it into CHIRP via **File → Import** and write it to your
radio.

Sample output:

```
[..] Fetched 29 rows from https://repetidores.pt/json_vhf.php
[..] Fetched 58 rows from https://repetidores.pt/json_uhf.php
[..] Fetched 148 FM rows from https://portaldoradioamador.pt/api/v1/repeaters/fact-repeater/?limit=500
[..] Fetched 137 FM rows from https://api.radioamador.info/api/repeaters?limit=500
[..] Removed 7 row(s) outside supported ranges 65.0-108.0MHz, 136.0-174.0MHz, 400.0-480.0MHz
[..] Removed 201 duplicate row(s) by (Name, Frequency)
[OK] Merged 163 rows into chirp.csv
```

Row counts move as the upstreams are edited, so treat those as
illustrative. A run where one feed is unavailable looks like this — note
it still produces a usable file:

```
[..] Fetched 29 rows from https://repetidores.pt/json_vhf.php
[..] Fetched 58 rows from https://repetidores.pt/json_uhf.php
[..] Fetched 148 FM rows from https://portaldoradioamador.pt/api/v1/repeaters/fact-repeater/?limit=500
[!!] Skipping https://api.radioamador.info/api/repeaters?limit=500: HTTPError: HTTP Error 530: <none>
[!!] 1 of 4 sources unavailable — merging the rest.
[..] Removed 7 row(s) outside supported ranges 65.0-108.0MHz, 136.0-174.0MHz, 400.0-480.0MHz
[..] Removed 84 duplicate row(s) by (Name, Frequency)
[OK] Merged 144 rows into chirp.csv
```

### Custom output path

To write the merged CSV somewhere else, edit the call inside `main()`
at the bottom of `HAM_Repeaters_CHIRP.py`:

```python
def main() -> None:
    clean_chirp_csvs(output_file="chirp.csv")


if __name__ == "__main__":
    main()
```

Or import the function from another script:

```python
from HAM_Repeaters_CHIRP import clean_chirp_csvs

clean_chirp_csvs(output_file="/path/to/merged.csv")
```

---

## Processing pipeline

The script applies the following transformations, in order:

| Step              | What it does                                                                 |
|-------------------|------------------------------------------------------------------------------|
| 1. Download       | Fetches each feed over HTTPS in memory; a failing feed is logged and skipped |
| 2. Adapt          | Maps each feed into CHIRP rows (tone-enabled, analogue duplex only)          |
| 3. Merge          | Concatenates rows; output header is the union of every source's fieldnames   |
| 4. Filter         | Drops rows where `Tone` is empty                                             |
| 5. Tone normalize | Mirrors `cToneFreq` → `rToneFreq`; forces `Tone = "Tone"`                     |
| 6. Defaults       | Fills empty `rToneFreq`/`cToneFreq` with `88.5`, empty `TStep` with `12.50`  |
| 7. DVCODE reset   | Clears `DVCODE` (analogue-only workflow)                                     |
| 8. Band filter    | Drops rows outside `65-108 MHz`, `136-174 MHz`, `400-480 MHz`                |
| 9. Deduplicate    | By `(Name, float(Frequency))`, first occurrence wins                          |
| 10. Sort          | By `Frequency` ascending                                                     |
| 11. Renumber      | Rewrites `Location` as a 0-based contiguous index                            |
| 12. Write         | Writes the merged CSV to `output_file`                                       |

---

## Module API

The script is split into single-responsibility helpers around the public
`clean_chirp_csvs` orchestrator. Each helper can also be imported on its
own if you want to reuse one stage in another tool.

| Function                                        | Purpose                                                                    |
|-------------------------------------------------|----------------------------------------------------------------------------|
| `clean_chirp_csvs(output_file)`                 | Public entry point — runs the full download + merge + clean pipeline       |
| `main()`                                        | Default `__main__` entry point (writes to `chirp.csv`)                     |
| `_http_get(url)`                                | HTTPS GET with the browser-style User-Agent and request timeout            |
| `_chirp_row(name, out_f, in_f, tone, mode, comment)` | Build a CHIRP row, deriving `Duplex`/`Offset` from the frequency pair |
| `_portaldoradioamador_entry_to_row(entry)`      | Map one portaldoradioamador.pt `fact-repeater` record into a CHIRP row     |
| `_fetch_portaldoradioamador_json(url)`          | Download the portaldoradioamador.pt API and adapt it into CHIRP rows       |
| `_repetidores_entry_to_row(entry)`              | Map one repetidores.pt `aaData` array into a CHIRP row                     |
| `_fetch_repetidores_json(url)`                  | Download a repetidores.pt `json_*.php` feed and adapt it                   |
| `_radioamador_repeater_to_row(entry)`           | Map one radioamador.info API entry into a CHIRP row (FM + CTCSS only)      |
| `_fetch_radioamador_json(url)`                  | Download the radioamador.info JSON and adapt it into CHIRP rows            |
| `_download_and_merge()`                         | Fetch every feed and return `(rows, union_of_fieldnames)`                  |
| `_normalize_row(row)` / `_normalize_rows(rows)` | Tone filter + field normalization (per-row and bulk)                       |
| `_row_frequency(row)`                           | Parse `Frequency` as `float` (0.0 fallback on bad input)                   |
| `_filter_by_supported_frequency(rows)`          | Drop rows whose frequency falls outside the supported radio bands          |
| `_dedupe_by_name_frequency(rows)`               | First-occurrence-wins dedup by `(Name, float(Frequency))`                  |
| `_renumber_locations(rows)`                     | Rewrite `Location` as a contiguous 0-based index                           |
| `_write_csv(output_file, fieldnames, rows)`     | `DictWriter` with `restval=""`, `extrasaction="ignore"`                    |

Module-level constants `_REPETIDORES_PT_VHF_URL`, `_REPETIDORES_PT_UHF_URL`,
`_PORTALDORADIOAMADOR_PT_URL`, `_RADIOAMADOR_INFO_URL`, `_HTTP_USER_AGENT`,
`_HTTP_TIMEOUT_SECONDS`, `_SOURCE_ERRORS`, `_CHIRP_HEADER`,
`_CSV_LINE_TERMINATOR`, `_REQUIRED_FIELDS`, `_DEFAULT_TONE_FREQ`,
`_DEFAULT_TSTEP`, and `_SUPPORTED_FREQ_RANGES` centralize the remote
endpoints, HTTP request settings, the exception types treated as "this
feed gave us nothing usable", the canonical CHIRP header, the line
ending written to the output CSV, the columns the pipeline writes to,
the safe numeric defaults injected when a source row leaves them blank,
and the radio bands accepted by the band filter.

---

## Errors

- `ValueError: No valid tone-enabled rows found. Output file not
  generated.` — every row was filtered out (no tone), so nothing is
  written and any existing output file is left untouched.
- `RuntimeError: All repeater sources failed to download ...` — every
  feed was unreachable or returned unusable data. A single failing feed
  is logged and skipped instead; see
  [When a source is down](#when-a-source-is-down).

---

## License

Released under the [MIT License](LICENSE) — © 2026 xhico.

You are free to use, modify, and redistribute this code, including in
commercial projects, as long as the copyright notice and the license
text are preserved. The software is provided "as is", without warranty
of any kind.
