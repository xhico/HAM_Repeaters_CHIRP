"""
chirp_to_html
=============

Render a generated chirp.csv as a single self-contained HTML page, for
reading in a browser and printing to PDF.

    python3 chirp_to_html.py [chirp.csv] [chirp.html]

Every CHIRP column is shown, with each callsign linking to its record in
ANACOM's registry when the rows come from HAM_Repeaters_CHIRP.py. The page
itself needs no network access and no assets: open the file and it works. A filter box narrows the table as you
type, and the print stylesheet lays it out landscape with the header
repeated on each page, so File -> Print -> Save as PDF gives a usable
reference sheet.

The output mirrors whatever is in the CSV, distances included, so treat
chirp.html as private in the same way as chirp.csv.
"""

import csv
import html
import re
import sys
from datetime import datetime
from urllib.parse import quote

_DEFAULT_INPUT = "chirp.csv"
_DEFAULT_OUTPUT = "chirp.html"

# Each callsign links to its entry in ANACOM's registry. The detail page
# answers to a plain GET on this id — no session needed — which is what
# makes a durable link possible at all.
_ANACOM_DETAIL_URL = "https://www.anacom.pt/saas/detalhes-eucla.do?detailsBean.euclaId={eucla_id}"

# Rows carry ANACOM's record id under this key when HAM_Repeaters_CHIRP.py
# built them. It is not a CHIRP column and never reaches the CSV, so a
# page rendered from a CSV alone simply shows callsigns without links.
ANACOM_ID_FIELD = "_anacom_id"

# The CHIRP columns worth reading on screen. The rest — Duplex, Tone,
# DtcsCode, the D-STAR fields and so on — are constant or empty across the
# whole list, so they only cost width. They all remain in the CSV.
_DISPLAY_COLUMNS: tuple[str, ...] = (
    "Location", "Name", "Frequency", "Offset", "rToneFreq", "cToneFreq", "Comment",
)

# Synthesized, not a CHIRP column: the distance the pipeline works out
# from HOME_CENTER_LAT / HOME_CENTER_LON.
_DISTANCE_COLUMN = "Distance"

# Rows carry that distance in km under this key when
# HAM_Repeaters_CHIRP.py built them. As a fallback — a page rendered from
# a CSV alone — it is read back off the end of the Comment.
DISTANCE_FIELD = "_distance_km"

# The trailing " - 123 km" the pipeline appends to Comment. It moves into
# its own column here, so it is stripped from the comment text.
# HAM_Repeaters_CHIRP.py reuses this to compare runs without treating a
# change of home position as a change to every channel.
COMMENT_DISTANCE_RE = re.compile(r"\s*-\s*(\d+(?:\.\d+)?)\s*km\s*$")

_STYLE = """
:root {
  --bg: #ffffff; --fg: #16181d; --muted: #6b7280;
  --line: #e3e6ea; --stripe: #f7f8fa; --accent: #1f6feb; --chip: #eef2f7;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14161a; --fg: #e6e8eb; --muted: #9aa3ad;
    --line: #2a2f36; --stripe: #191c21; --accent: #6aa9ff; --chip: #222831;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 1.5rem; background: var(--bg); color: var(--fg);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
header { margin-bottom: 1rem; }
h1 { margin: 0 0 .2rem; font-size: 1.35rem; letter-spacing: -.01em; }
.meta { color: var(--muted); font-size: .85rem; }
.controls { margin: 1rem 0 .75rem; display: flex; gap: .75rem; align-items: center; }
input[type="search"] {
  flex: 1 1 22rem; max-width: 30rem; padding: .5rem .7rem;
  border: 1px solid var(--line); border-radius: 7px;
  background: var(--bg); color: var(--fg); font-size: .95rem;
}
input[type="search"]:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
#count { color: var(--muted); font-size: .85rem; white-space: nowrap; }
.scroll { overflow-x: auto; border: 1px solid var(--line); border-radius: 9px; }
table { border-collapse: collapse; width: 100%; font-size: .82rem; }
th, td {
  padding: .4rem .55rem; text-align: left; white-space: nowrap;
  border-bottom: 1px solid var(--line);
}
th {
  position: sticky; top: 0; background: var(--bg); z-index: 1;
  font-weight: 600; font-size: .72rem; text-transform: uppercase;
  letter-spacing: .04em; color: var(--muted);
}
tbody tr:nth-child(even) { background: var(--stripe); }
td.num { font-variant-numeric: tabular-nums; }
td.name { font-weight: 600; }
td.name a { color: var(--accent); text-decoration: none; }
td.name a:hover { text-decoration: underline; }
td.wide { white-space: normal; min-width: 11rem; width: 100%; }
td.dist { text-align: right; white-space: nowrap; }
th:last-child { text-align: right; }
tbody tr.hidden { display: none; }
footer { margin-top: 1rem; color: var(--muted); font-size: .8rem; }

@media print {
  @page { size: A4 portrait; margin: 12mm; }
  body { padding: 0; font-size: 9pt; background: #fff; color: #000; }
  .controls, footer { display: none; }
  .scroll { overflow: visible; border: 0; }
  table { font-size: 8.5pt; }
  thead { display: table-header-group; }   /* repeat the header on every page */
  tr { page-break-inside: avoid; }
  th { position: static; color: #000; }
  td.name a { color: #000; text-decoration: none; }
  tbody tr:nth-child(even) { background: #f2f2f2; -webkit-print-color-adjust: exact; }
}
"""

_SCRIPT = """
(function () {
  var box = document.getElementById('filter');
  var rows = Array.prototype.slice.call(
    document.querySelectorAll('tbody tr'));
  var count = document.getElementById('count');
  var total = rows.length;

  function apply() {
    var q = box.value.trim().toLowerCase();
    var shown = 0;
    rows.forEach(function (row) {
      var hit = q === '' || row.dataset.search.indexOf(q) !== -1;
      row.classList.toggle('hidden', !hit);
      if (hit) shown++;
    });
    count.textContent = shown === total
      ? total + ' channels'
      : shown + ' of ' + total + ' channels';
  }

  box.addEventListener('input', apply);
  apply();
})();
"""


def _read_rows(path: str) -> tuple[list[dict[str, str]], list[str]]:
    """Read the CHIRP CSV, returning "(rows, fieldnames)"."""
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        return list(reader), fieldnames


def _cell_class(column: str) -> str:
    """Pick the CSS class for a column's cells."""
    if column == "Name":
        return "name"
    if column == "Comment":
        return "wide"
    if column == _DISTANCE_COLUMN:
        return "num dist"
    return "num"


def _split_distance(row: dict[str, str]) -> tuple[str, str]:
    """
    Return "(comment without its distance, distance for its own column)".

    Prefers the value the pipeline attached to the row; failing that —
    rendering straight from a CSV — reads it back off the comment, which
    is where the pipeline also writes it for the radio to show.
    """
    comment = (row.get("Comment") or "").strip()
    match = COMMENT_DISTANCE_RE.search(comment)
    if match is not None:
        comment = comment[: match.start()].rstrip()

    raw = (row.get(DISTANCE_FIELD) or "").strip() or (match.group(1) if match else "")
    if not raw:
        return comment, ""
    try:
        return comment, f"{float(raw):.0f} km"
    except ValueError:
        return comment, ""


def _cell_html(column: str, value: str, eucla_id: str) -> str:
    """
    Render one cell, linking the callsign to its ANACOM record.

    Everything else is plain escaped text. Without an id — a page rendered
    straight from a CSV — the callsign is shown unlinked rather than
    pointing somewhere that cannot resolve.
    """
    if column != "Name" or not value or not eucla_id:
        return html.escape(value)
    url = _ANACOM_DETAIL_URL.format(eucla_id=quote(eucla_id, safe=""))
    return (
        f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">'
        f"{html.escape(value)}</a>"
    )


def build_html(rows: list[dict[str, str]], source: str) -> str:
    """Render the rows as one self-contained HTML document."""
    columns = _DISPLAY_COLUMNS + (_DISTANCE_COLUMN,)
    head = "".join(f"<th>{html.escape(name)}</th>" for name in columns)

    body = []
    for row in rows:
        comment, distance = _split_distance(row)
        shown = {**row, "Comment": comment, _DISTANCE_COLUMN: distance}
        # A lowercased copy of every shown field backs the filter box, so
        # typing a callsign, a locator or a tone all narrow the table.
        haystack = " ".join((shown.get(name) or "") for name in columns).lower()
        eucla_id = row.get(ANACOM_ID_FIELD) or ""
        cells = "".join(
            f'<td class="{_cell_class(name)}">'
            f'{_cell_html(name, shown.get(name) or "", eucla_id)}</td>'
            for name in columns
        )
        body.append(f'<tr data-search="{html.escape(haystack, quote=True)}">{cells}</tr>')

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HAM Repeaters — CHIRP channel list</title>
<style>{_STYLE}</style>
</head>
<body>
<header>
  <h1>HAM Repeaters — CHIRP channel list</h1>
  <div class="meta">{len(rows)} channels from {html.escape(source)} · generated {generated}</div>
</header>

<div class="controls">
  <input id="filter" type="search" placeholder="Filter by callsign, frequency, tone, locator, holder…" autocomplete="off">
  <span id="count"></span>
</div>

<div class="scroll">
  <table>
    <thead><tr>{head}</tr></thead>
    <tbody>
{chr(10).join(body)}
    </tbody>
  </table>
</div>

<footer>Print or save as PDF with File → Print — the table prints portrait with the header repeated on each page.</footer>
<script>{_SCRIPT}</script>
</body>
</html>
"""


def main() -> None:
    """Entry point: read the CSV given (or chirp.csv) and write the HTML."""
    source = sys.argv[1] if len(sys.argv) > 1 else _DEFAULT_INPUT
    target = sys.argv[2] if len(sys.argv) > 2 else _DEFAULT_OUTPUT
    try:
        rows, _ = _read_rows(source)
    except FileNotFoundError:
        print(f"[!!] No such file: {source}. Run HAM_Repeaters_CHIRP.py first.")
        sys.exit(1)
    except OSError as exc:
        print(f"[!!] Could not read {source}: {exc}")
        sys.exit(1)

    if not rows:
        print(f"[!!] {source} has no channels — nothing to render.")
        sys.exit(1)

    with open(target, "w", encoding="utf-8") as f:
        f.write(build_html(rows, source))
    print(f"[OK] Wrote {len(rows)} channels to {target}")


if __name__ == "__main__":
    main()
