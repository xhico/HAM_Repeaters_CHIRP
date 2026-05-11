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

On every run the script fetches all three of the following sources over
HTTPS and merges them in memory — nothing is cached to disk except the
final output CSV.

| Source                                                                  | Format             | Notes                                                                |
|-------------------------------------------------------------------------|--------------------|----------------------------------------------------------------------|
| `https://repetidores.pt/gerarcsv.php`                                   | CHIRP CSV          | Consumed as-is.                                                      |
| `https://portaldoradioamador.pt/backend/repeaters/export/chirp/`        | CHIRP CSV          | Consumed as-is.                                                      |
| `https://api.radioamador.info/api/repeaters?limit=500`                  | JSON               | Adapted into CHIRP rows: FM-mode entries with a CTCSS tone only.     |

Both upstream servers reject the default `Python-urllib` User-Agent with
HTTP 403, so a generic browser-style UA is sent on every request.

---

## Features

- **Download** — fetches every source over HTTPS in memory; no files
  written to disk besides the final merged CSV.
- **Adapt** — converts the radioamador.info JSON payload into CHIRP rows
  (output/input frequency → `Frequency`/`Duplex`/`Offset`, CTCSS tone
  copied into `rToneFreq`/`cToneFreq`, `Mode=FM`).
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
  entry. The first occurrence wins (sources are queried in the order
  listed above).
- **Sort** — orders the surviving rows by `Frequency` ascending.
- **Renumber** — rewrites the `Location` column as a contiguous 0-based
  sequence reflecting the final order.

---

## Requirements

- Python **3.10+** (uses builtin generics like `list[str]` / `set[tuple[...]]`
  and the PEP 604 `X | None` union syntax)
- No third-party dependencies — only the Python standard library
  (`csv`, `io`, `json`, `urllib.request`)
- Outbound HTTPS access to the three source hosts listed above.

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
├── HAM_Repeaters_CHIRP.py        # the download + merge + clean script
└── chirp.csv      # generated output (after running the script)
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
[..] Fetched 87 rows from https://repetidores.pt/gerarcsv.php
[..] Fetched 143 rows from https://portaldoradioamador.pt/backend/repeaters/export/chirp/
[..] Fetched 137 FM rows from https://api.radioamador.info/api/repeaters?limit=500
[..] Removed 2 row(s) outside supported ranges 65.0-108.0MHz, 136.0-174.0MHz, 400.0-480.0MHz
[..] Removed 201 duplicate row(s) by (Name, Frequency)
[OK] Merged 163 rows into chirp.csv
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

| Step              | What it does                                                                       |
|-------------------|------------------------------------------------------------------------------------|
| 1. Download       | Fetches each remote source over HTTPS in memory                                    |
| 2. Adapt          | Maps the radioamador.info JSON entries into CHIRP rows (FM + CTCSS only)           |
| 3. Merge          | Concatenates rows; output header is the union of every source's fieldnames        |
| 4. Filter         | Drops rows where `Tone` is empty                                                   |
| 5. Tone normalize | Mirrors `cToneFreq` → `rToneFreq`; forces `Tone = "Tone"`                          |
| 6. Defaults       | Fills empty `rToneFreq`/`cToneFreq` with `88.5`, empty `TStep` with `12.50`        |
| 7. DVCODE reset   | Clears `DVCODE` (analogue-only workflow)                                           |
| 8. Band filter    | Drops rows outside `65-108 MHz`, `136-174 MHz`, `400-480 MHz`                      |
| 9. Deduplicate    | By `(Name, float(Frequency))`, first occurrence wins                               |
| 10. Sort          | By `Frequency` ascending                                                           |
| 11. Renumber      | Rewrites `Location` as a 0-based contiguous index                                  |
| 12. Write         | Writes the merged CSV to `output_file`                                             |

---

## Module API

The script is split into single-responsibility helpers around the public
`clean_chirp_csvs` orchestrator. Each helper can also be imported on its
own if you want to reuse one stage in another tool.

| Function                                              | Purpose                                                                          |
|-------------------------------------------------------|----------------------------------------------------------------------------------|
| `clean_chirp_csvs(output_file)`                       | Public entry point — runs the full download + merge + clean pipeline             |
| `main()`                                              | Default `__main__` entry point (writes to `chirp.csv`)            |
| `_http_get(url)`                                      | HTTPS GET with the browser-style User-Agent and request timeout                  |
| `_fetch_chirp_csv(url)`                               | Download a CHIRP-format CSV; return `(rows, fieldnames)`                         |
| `_radioamador_repeater_to_row(entry)`                 | Map a single radioamador.info API entry into a CHIRP row (FM + CTCSS only)       |
| `_fetch_radioamador_json(url)`                        | Download the radioamador.info JSON and adapt it into CHIRP rows                  |
| `_download_and_merge()`                               | Fetch every source and return `(rows, union_of_fieldnames)`                      |
| `_normalize_row(row)` / `_normalize_rows(rows)`       | Tone filter + field normalization (per-row and bulk)                             |
| `_row_frequency(row)`                                 | Parse `Frequency` as `float` (0.0 fallback on bad input)                         |
| `_is_supported_frequency(freq)` / `_filter_by_supported_frequency(rows)` | Drop rows whose frequency falls outside the supported radio bands |
| `_dedupe_by_name_frequency(rows)`                     | First-occurrence-wins dedup by `(Name, float(Frequency))`                        |
| `_sort_by_frequency(rows)`                            | In-place sort by `Frequency` ascending                                           |
| `_renumber_locations(rows)`                           | Rewrite `Location` as a contiguous 0-based index                                 |
| `_write_csv(output_file, fieldnames, rows)`           | `DictWriter` with `restval=""`, `extrasaction="ignore"`                          |

Module-level constants `_REPETIDORES_PT_URL`, `_PORTALDORADIOAMADOR_PT_URL`,
`_RADIOAMADOR_INFO_URL`, `_HTTP_USER_AGENT`, `_HTTP_TIMEOUT_SECONDS`,
`_CHIRP_HEADER`, `_REQUIRED_FIELDS`, `_DEFAULT_TONE_FREQ`,
`_DEFAULT_TSTEP`, and `_SUPPORTED_FREQ_RANGES` centralize the remote
endpoints, HTTP request settings, canonical CHIRP header, columns the
pipeline writes to, the safe numeric defaults injected when a source row
leaves them blank, and the radio bands accepted by the band filter.

---

## Errors

- `ValueError: No valid tone-enabled rows found. Output file not
  generated.` — every row was filtered out (no tone), so nothing is
  written.
- `urllib.error.URLError` / `urllib.error.HTTPError` — a remote source
  is unreachable or returned a non-2xx response. Check connectivity to
  the source hosts listed above.

---

## License

Released under the [MIT License](LICENSE) — © 2026 xhico.

You are free to use, modify, and redistribute this code, including in
commercial projects, as long as the copyright notice and the license
text are preserved. The software is provided "as is", without warranty
of any kind.
