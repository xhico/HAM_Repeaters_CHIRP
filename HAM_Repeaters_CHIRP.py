"""
HAM_Repeaters_CHRIP
=================

Merge and normalize multiple CHIRP CSV exports into a single, clean CSV
suitable for programming an analogue radio.

The script discovers every "*.csv" file inside an input directory, merges
their contents, drops rows that do not carry a CTCSS/tone configuration,
deduplicates by "(Name, Frequency)", sorts the result by "Frequency"
ascending, renumbers the "Location" column, and writes the merged file.
"""

import csv
import glob
import os

Row = dict[str, str]

# Fields the pipeline writes to. They must always exist in the output
# header, even if none of the input CSVs contained them.
_REQUIRED_FIELDS: tuple[str, ...] = (
    "Tone",
    "rToneFreq",
    "cToneFreq",
    "TStep",
    "DVCODE",
    "Location",
)

# Safe defaults for numeric columns that CHIRP refuses to parse blank.
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


def _discover_csv_files(input_dir: str) -> list[str]:
    """Return a sorted list of "*.csv" paths inside "input_dir"."""
    # Sorted for deterministic merge order across runs, which also makes
    # the "first occurrence wins" dedup rule predictable.
    input_files = sorted(glob.glob(os.path.join(input_dir, "*.csv")))
    if not input_files:
        raise FileNotFoundError(f"No CSV files found in: {input_dir}")
    return input_files


def _load_and_merge_csvs(input_files: list[str]) -> tuple[list[Row], list[str]]:
    """
    Read every file in "input_files" and return "(rows, fieldnames)".

    The output fieldnames are the **union** of every input's header,
    ordered by first occurrence (first file's columns first, new columns
    from later files appended in the order they appear). Fields the
    pipeline writes to are guaranteed to be present.
    """
    rows: list[Row] = []
    fieldnames: list[str] = []
    seen_fields: set[str] = set()

    for input_file in input_files:
        with open(input_file, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for name in reader.fieldnames or []:
                if name not in seen_fields:
                    seen_fields.add(name)
                    fieldnames.append(name)
            rows.extend(reader)
        print(f"[..] Loaded: {input_file}")

    for required in _REQUIRED_FIELDS:
        if required not in seen_fields:
            seen_fields.add(required)
            fieldnames.append(required)

    return rows, fieldnames


def _normalize_row(row: Row) -> Row | None:
    """
    Apply tone filtering and field normalization to a single row.

    Returns the (mutated) row if it should be kept, or "None" if it was
    filtered out (no CTCSS tone).
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
    """Apply "_normalize_row" to every row, dropping filtered ones."""
    normalized: list[Row] = []
    for row in rows:
        kept = _normalize_row(row)
        if kept is not None:
            normalized.append(kept)
    return normalized


def _row_frequency(row: Row) -> float:
    """Return "Frequency" as a float, falling back to 0.0 on bad input."""
    try:
        return float(row.get("Frequency", "0") or 0)
    except ValueError:
        # Malformed Frequency — fall back to 0.0 so the row still
        # participates in dedup/sort rather than crashing the run.
        return 0.0


def _is_supported_frequency(freq: float) -> bool:
    """Return True if "freq" (MHz) falls inside any supported band."""
    return any(low <= freq <= high for low, high in _SUPPORTED_FREQ_RANGES)


def _filter_by_supported_frequency(rows: list[Row]) -> list[Row]:
    """
    Drop rows whose "Frequency" lies outside every supported band.

    The radio (and CHIRP) will refuse to write a row whose frequency does
    not fall within one of "_SUPPORTED_FREQ_RANGES", so we strip them
    here rather than letting the import fail later.
    """
    kept: list[Row] = []
    dropped = 0
    for row in rows:
        if _is_supported_frequency(_row_frequency(row)):
            kept.append(row)
        else:
            dropped += 1
    if dropped:
        bands = ", ".join(f"{low}-{high}MHz" for low, high in _SUPPORTED_FREQ_RANGES)
        print(f"[..] Removed {dropped} row(s) outside supported ranges {bands}")
    return kept


def _dedupe_by_name_frequency(rows: list[Row]) -> list[Row]:
    """
    Deduplicate rows by "(Name, float(Frequency))", first occurrence wins.

    Frequency is normalized to a float for the dedup key so that values
    like "51.9900" and "51.990000" — which differ as strings but match as
    numbers — are treated as the same entry.
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
    """Sort rows in-place by "Frequency" ascending."""
    rows.sort(key=_row_frequency)


def _renumber_locations(rows: list[Row]) -> None:
    """
    Rewrite the "Location" column as a contiguous 1-based index.

    CHIRP expects "Location" to be a contiguous 1-based sequence; the
    source files each start at 1, so concatenation produces collisions
    that we rewrite here.
    """
    for i, row in enumerate(rows, start=1):
        row["Location"] = str(i)


def _write_csv(output_file: str, fieldnames: list[str], rows: list[Row]) -> None:
    """Write "rows" to "output_file" using "fieldnames" as the header."""
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, restval="", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def clean_chirp_csvs(input_dir: str, output_file: str) -> None:
    """
    Merge and normalize all CHIRP CSV exports inside "input_dir" into a
    single cleaned CSV at "output_file".

    The pipeline applies the following transformations, in order:

    1. **Discovery** — every "*.csv" file in "input_dir" is loaded and
       concatenated. The output header is the **union** of every input's
       fieldnames, preserving the order of the first file and appending
       any new columns as they appear in later files. Inputs are not
       required to share an identical CHIRP schema (e.g. some exports
       include "DVCODE" and others do not). Rows missing a column are
       written with an empty string for that column, and any stray keys
       not in the merged header are ignored in writing.
    2. **Filtering** — rows without a "Tone" value are dropped, since
       analogue repeater entries without a CTCSS tone are not useful for
       this workflow.
    3. **Tone normalization** — when "cToneFreq" is present, it is copied
       into "rToneFreq" so both fields stay consistent, and "Tone" is
       forced to the literal string ""Tone"".
    4. **Defensive defaults** — any empty "rToneFreq" / "cToneFreq" /
       "TStep" field is filled with a safe default, because CHIRP refuses
       to parse rows where these numeric columns are blank.
    5. **DVCODE reset** — the digital-mode field is cleared, since this
       workflow targets analogue (FM) repeaters only.
    6. **Frequency-range filter** — rows whose "Frequency" falls outside
       the supported radio bands (65-108 MHz, 136-174 MHz, 400-480 MHz)
       are dropped, since CHIRP and the target radio reject them with
       "Frequency X is out of supported ranges ..." errors.
    7. **Deduplication** — rows are deduplicated by "(Name, Frequency)",
       comparing "Frequency" as a float so values like "51.9900" and
       "51.990000" collapse into one entry. The first occurrence wins.
    8. **Sorting** — surviving rows are sorted by "Frequency" ascending.
    9. **Renumbering** — the "Location" column is rewritten as a 1-based
       sequence reflecting the final sorted order.

    Parameters
    ----------
    input_dir : str
        Directory containing the raw CHIRP CSV exports to merge.
    output_file : str
        Destination path for the merged, cleaned CSV.

    Raises
    ------
    FileNotFoundError
        If "input_dir" contains no "*.csv" files.
    ValueError
        If, after filtering, no tone-enabled rows remain to write.

    Returns
    -------
    None
    """
    input_files = _discover_csv_files(input_dir)
    rows, fieldnames = _load_and_merge_csvs(input_files)

    rows = _normalize_rows(rows)
    rows = _filter_by_supported_frequency(rows)
    rows = _dedupe_by_name_frequency(rows)
    _sort_by_frequency(rows)
    _renumber_locations(rows)

    if not rows:
        raise ValueError("No valid tone-enabled rows found. Output file not generated.")

    _write_csv(output_file, fieldnames, rows)
    print(f"[OK] Merged {len(input_files)} files into {len(rows)} rows: {output_file}")


def main() -> None:
    """Default entry point: merge "chirp_files/" into "ham_repeaters_merged.csv"."""
    clean_chirp_csvs(
        input_dir="chirp_files",
        output_file="ham_repeaters_merged.csv",
    )


if __name__ == "__main__":
    main()
