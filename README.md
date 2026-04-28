# **HAM Repeaters** CHIRP

A small Python utility that merges multiple [CHIRP](https://chirpmyradio.com/)
CSV exports into a single, cleaned, deduplicated CSV ready to be imported
back into CHIRP and flashed onto an analogue radio.

The tool is aimed at amateur radio operators who collect repeater lists
from several sources (e.g. national directories, regional clubs, personal
exports) and want one consolidated channel list with consistent CTCSS
tones, no duplicates, sorted by frequency.

---

## Features

- **Merge** — concatenates every `*.csv` file inside an input directory.
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
  entry. The first occurrence wins.
- **Sort** — orders the surviving rows by `Frequency` ascending.
- **Renumber** — rewrites the `Location` column as a contiguous 1-based
  sequence reflecting the final order.

---

## Requirements

- Python **3.10+** (uses builtin generics like `list[str]` / `set[tuple[...]]`
  and the PEP 604 `X | None` union syntax)
- No third-party dependencies — only the Python standard library
  (`csv`, `glob`, `os`)

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
Python 3.9+ interpreter.

---

## Project layout

```
HAM_Repeaters_CHIRP/
├── HAM_Repeaters_CHIRP.py        # the merge + clean script
├── chirp_files/                  # drop your CHIRP CSV exports here
│   ├── 1.csv
│   ├── 2.csv
│   └── 3.csv
└── ham_repeaters_merged.csv      # generated output (after running the script)
```

Input CSVs do **not** need to share an identical header — different CHIRP
exports sometimes include extra columns (e.g. `DVCODE`) and sometimes
omit them. The output header is the **union** of every input's
fieldnames, ordered by first occurrence: columns from the first file
come first, and any new columns introduced by later files are appended
in the order they appear. Rows missing a column are written with an
empty string for that column.

A typical CHIRP header looks like:
`Location,Name,Frequency,Duplex,Offset,Tone,rToneFreq,cToneFreq,DtcsCode,DtcsPolarity,Mode,TStep,Skip,Comment,URCALL,RPT1CALL,RPT2CALL,DVCODE`.

---

## Usage

1. Place every CHIRP CSV export you want to merge inside `chirp_files/`.
2. From the project root, run:

   ```bash
   python3 HAM_Repeaters_CHIRP.py
   ```

3. The merged file `ham_repeaters_merged.csv` is written to the project
   root. Import it into CHIRP via **File → Import** and write it to your
   radio.

Sample output:

```
[..] Loaded: chirp_files/1.csv
[..] Loaded: chirp_files/2.csv
[..] Loaded: chirp_files/3.csv
[..] Removed 218 duplicate row(s) by (Name, Frequency)
[OK] Merged 3 files into 170 rows: ham_repeaters_merged.csv
```

### Custom paths

To use a different input directory or output file, edit the call inside
`main()` at the bottom of `HAM_Repeaters_CHIRP.py`:

```python
def main() -> None:
    clean_chirp_csvs(
        input_dir="chirp_files",
        output_file="ham_repeaters_merged.csv",
    )


if __name__ == "__main__":
    main()
```

Or import the function from another script:

```python
from HAM_Repeaters_CHIRP import clean_chirp_csvs

clean_chirp_csvs(
    input_dir="/path/to/exports",
    output_file="/path/to/merged.csv",
)
```

---

## Processing pipeline

The script applies the following transformations, in order:

| Step              | What it does                                                                |
|-------------------|-----------------------------------------------------------------------------|
| 1. Discovery      | Globs `*.csv` inside `input_dir`, sorted for deterministic order            |
| 2. Load & merge   | Concatenates rows; output header is the union of every input's fieldnames   |
| 3. Filter         | Drops rows where `Tone` is empty                                            |
| 4. Tone normalize | Mirrors `cToneFreq` → `rToneFreq`; forces `Tone = "Tone"`                   |
| 5. Defaults       | Fills empty `rToneFreq`/`cToneFreq` with `88.5`, empty `TStep` with `12.50` |
| 6. DVCODE reset   | Clears `DVCODE` (analogue-only workflow)                                    |
| 7. Band filter    | Drops rows outside `65-108 MHz`, `136-174 MHz`, `400-480 MHz`               |
| 8. Deduplicate    | By `(Name, float(Frequency))`, first occurrence wins                        |
| 9. Sort           | By `Frequency` ascending                                                    |
| 10. Renumber      | Rewrites `Location` as a 1-based contiguous index                           |
| 11. Write         | Writes the merged CSV to `output_file`                                      |

---

## Module API

The script is split into single-responsibility helpers around the public
`clean_chirp_csvs` orchestrator. Each helper can also be imported on its
own if you want to reuse one stage in another tool.

| Function                                              | Purpose                                                                         |
|-------------------------------------------------------|---------------------------------------------------------------------------------|
| `clean_chirp_csvs(input_dir, output_file)`            | Public entry point — runs the full pipeline                                     |
| `main()`                                              | Default `__main__` entry point (wires `chirp_files/` → `ham_repeaters_merged.csv`) |
| `_discover_csv_files(input_dir)`                      | Glob + sort `*.csv` paths; raises `FileNotFoundError` if empty                  |
| `_load_and_merge_csvs(input_files)`                   | Read every CSV; return `(rows, union_of_fieldnames)`                            |
| `_normalize_row(row)` / `_normalize_rows(rows)`       | Tone filter + field normalization (per-row and bulk)                            |
| `_row_frequency(row)`                                 | Parse `Frequency` as `float` (0.0 fallback on bad input)                        |
| `_is_supported_frequency(freq)` / `_filter_by_supported_frequency(rows)` | Drop rows whose frequency falls outside the supported radio bands |
| `_dedupe_by_name_frequency(rows)`                     | First-occurrence-wins dedup by `(Name, float(Frequency))`                       |
| `_sort_by_frequency(rows)`                            | In-place sort by `Frequency` ascending                                          |
| `_renumber_locations(rows)`                           | Rewrite `Location` as a contiguous 1-based index                                |
| `_write_csv(output_file, fieldnames, rows)`           | `DictWriter` with `restval=""`, `extrasaction="ignore"`                         |

Module-level constants `_REQUIRED_FIELDS`, `_DEFAULT_TONE_FREQ`,
`_DEFAULT_TSTEP`, and `_SUPPORTED_FREQ_RANGES` centralize the columns
the pipeline writes to, the safe numeric defaults injected when a source
row leaves them blank, and the radio bands accepted by the band filter.

---

## Errors

- `FileNotFoundError: No CSV files found in: <dir>` — the input directory
  exists but contains no `*.csv` files.
- `ValueError: No valid tone-enabled rows found. Output file not
  generated.` — every row was filtered out (no tone), so nothing is
  written.

---

## License

No licence specified. Treat as personal-use code unless the repository
owner adds one.
