"""Turns a finished WIEC component run folder into one HTML summary.

    python3 -m WIEC_Component.wiec_report                    newest run
    python3 -m WIEC_Component.wiec_report <run folder> ...
    python3 -m WIEC_Component.wiec_report --all              every run

run_wiec_component.finish() writes it at the end of every run, so the summary is
already sitting in the run folder; the command line is for rebuilding an old one
after the template changes. Run it from the repository root (it reads config.json).

The HTML is **self-contained**: the plots are embedded as base64, so the file can be
mailed or dropped on a share and still render. It reads only what the run wrote:

    run.json            the whole session datastore, including every measurement row
    plots/<test>/       the PNGs each test saved (searched recursively)

-------------------------------------------------------------------------------------------------------
config.json keys used:

"WIEC_results_folder"                                Folder holding runs/<run id>/ (for --all / newest)
"wiec_run_json_name"                                 Name of the run JSON in each run folder
"wiec_plots_folder"                                  Name of the plots folder in each run folder
"wiec_results_csv_name"                              Name of the measurements CSV (linked in the footer)
"wiec_session_log_name"                              Name of the session log (linked in the footer)
"wiec_report_name"                                   Name of the HTML summary written into each run folder
"rounding_factor"                                    Decimal places shown for measured values
-------------------------------------------------------------------------------------------------------
"""

import base64
import glob
import html
import json
import os
import sys
from datetime import datetime

from . import WIEC_utils


CONFIG_FILE = "config.json"

# Measurement "test" names that are recorded inside another item, shown under that item
TEST_GROUPS = {"wiec_wib_qc": ["ptc_power", "wib_checkout", "femb_checkout"]}

# Headings for the measurement tests that are not items themselves
SECTION_NAMES = {"ptc_power": "PTC Power", "wib_checkout": "WIB Checkout", "femb_checkout": "FEMB Checkout"}

# Measurement row field -> column heading, in the order the columns are shown
ROW_COLUMNS = [("wib_slot", "WIB"), ("femb_position", "FEMB"), ("femb_serial", "FEMB serial"),
               ("channel", "Channel"), ("measurement", "Measurement"), ("result", "Result"),
               ("value", "Value"), ("unit", "Unit"), ("min", "Min"), ("max", "Max"),
               ("setpoint", "Setpoint"), ("note", "Note")]

# Tables with more rows than this start folded up
FOLD_ROWS = 40


def _pale(hex_color):
    """CSS colour for a pale cell fill, the same shade as the QC sheet's cells."""
    return "#" + WIEC_utils.cell_rgb(hex_color, WIEC_utils.CELL_OPACITY)[2:]


# Every colour is a WIEC_utils scheme colour (full or faded towards white), white, or black at an opacity
ROOT_CSS = (f":root {{ --pass: {WIEC_utils.PASS_HEX}; --fail: {WIEC_utils.FAIL_HEX}; "
            f"--pass-fill: {_pale(WIEC_utils.PASS_HEX)}; --fail-fill: {_pale(WIEC_utils.FAIL_HEX)}; "
            f"--na-fill: {_pale(WIEC_utils.BAR_EMPTY_HEX)}; --head: {_pale(WIEC_utils.HEADER_HEX)}; "
            f"--title: {WIEC_utils.BANNER_BACKGROUND_HEX}; --link: {WIEC_utils.ACTION_HEX}; }}")

CSS = """
:root { --bg: #ffffff; --fg: #000000; --muted: rgba(0, 0, 0, .6); --line: rgba(0, 0, 0, .12); }
* { box-sizing: border-box; }
body { margin: 0; padding: 2rem 1.5rem 4rem; background: var(--bg); color: var(--fg);
       font: 15px/1.55 -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif; }
.wrap { max-width: 1100px; margin: 0 auto; }
h1 { font-size: 1.55rem; margin: 0 0 .3rem; color: var(--title); }
h2 { font-size: 1.15rem; margin: 2.4rem 0 .8rem; padding-bottom: .35rem;
     border-bottom: 1px solid var(--line); }
h3 { font-size: 1rem; margin: 1.6rem 0 .6rem; }
a { color: var(--link); }
.sub { color: var(--muted); margin: 0 0 1.4rem; }
.verdict { padding: 0 .35rem; }
.facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
         gap: .6rem; margin: 1.2rem 0; }
.fact { border: 1px solid var(--line); padding: .6rem .8rem; }
.fact .k { color: var(--muted); font-size: .78rem; text-transform: uppercase;
           letter-spacing: .05em; }
.fact .v { font-size: 1.05rem; font-weight: 600; margin-top: .15rem;
           word-break: break-word; }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: .9rem; }
th, td { text-align: left; padding: .42rem .6rem; border-bottom: 1px solid var(--line);
         vertical-align: top; }
th { background: var(--head); font-weight: 600; position: sticky; top: 0; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.PASS { color: var(--pass); background: var(--pass-fill); font-weight: 700; }
.FAIL { color: var(--fail); background: var(--fail-fill); font-weight: 700; }
.NA   { color: var(--muted); background: var(--na-fill); }
details { margin: .8rem 0; }
summary { cursor: pointer; color: var(--muted); }
.plots { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 1rem; }
figure { margin: 0; }
figure img { width: 100%; height: auto; border: 1px solid var(--line); }
figcaption { color: var(--muted); font-size: .85rem; margin-top: .3rem; word-break: break-all; }
.note { border-left: 3px solid var(--line); padding: .5rem .9rem; color: var(--muted);
        margin: 1rem 0; font-size: .9rem; }
footer { margin-top: 3rem; color: var(--muted); font-size: .82rem; }
"""


# ----------------------------------------------------------------------
def load_config(path=CONFIG_FILE):
    with open(path) as handle:
        return json.load(handle)


def _read_json(directory, config):
    path = os.path.join(directory, config["wiec_run_json_name"])
    with open(path) as handle:
        return json.load(handle)


def _embed(path):
    with open(path, "rb") as handle:
        return base64.b64encode(handle.read()).decode("ascii")


def _e(value):
    return html.escape("" if value is None else str(value))


def _fact(key, value):
    return f'<div class="fact"><div class="k">{_e(key)}</div><div class="v">{_e(value)}</div></div>'


def _table(headers, rows, numeric=()):
    out = ['<div class="scroll"><table><thead><tr>']
    out += [f"<th>{_e(h)}</th>" for h in headers]
    out.append("</tr></thead><tbody>")
    for row in rows:
        out.append("<tr>")
        for index, cell in enumerate(row):
            css = ' class="num"' if index in numeric else ""
            if cell in ("PASS", "FAIL", "not tested"):
                css = f' class="{"NA" if cell == "not tested" else cell}"'
            out.append(f"<td{css}>{_e(cell)}</td>")
        out.append("</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def _fold(summary, content, rows):
    """Puts content in a <details> fold when it has more than FOLD_ROWS rows."""
    if rows <= FOLD_ROWS:
        return content
    return f"<details><summary>{_e(summary)}</summary>{content}</details>"


def _number(value, places):
    if isinstance(value, float) and places is not None:
        return round(value, places)
    return value


def _measurement_table(rows, places):
    """Table of measurement rows. Columns that are empty in every row are left out."""
    columns = [(key, title) for key, title in ROW_COLUMNS
               if any(row.get(key) not in (None, "") for row in rows)]
    numeric = [i for i, (key, _) in enumerate(columns) if key in ("value", "min", "max", "setpoint")]
    table_rows = [[_number(row.get(key), places) for key, _ in columns] for row in rows]
    return _table([title for _, title in columns], table_rows, numeric=numeric)


def _verdict(passed):
    if passed is None:
        return "not tested"
    return "PASS" if passed else "FAIL"


def _verdict_span(text):
    """PASS / FAIL / anything else, in the same colours as the table cells."""
    css = text if text in ("PASS", "FAIL") else "NA"
    return f'<span class="verdict {css}">{_e(text)}</span>'


def _figures(directory, config, test):
    pattern = os.path.join(directory, config["wiec_plots_folder"], test, "**", "*.png")
    paths = sorted(glob.glob(pattern, recursive=True))
    if not paths:
        return ""
    plot_root = os.path.join(directory, config["wiec_plots_folder"], test)
    figures = []
    for path in paths:
        caption = os.path.relpath(path, plot_root)
        figures.append(f'<figure><img alt="{_e(caption)}" loading="lazy" '
                       f'src="data:image/png;base64,{_embed(path)}">'
                       f'<figcaption>{_e(caption)}</figcaption></figure>')
    return f'<div class="plots">{"".join(figures)}</div>'


def _femb_section(rows, places):
    """FEMB checkout: one block per WIB slot, a line per FEMB, then its failed checks."""
    parts = []
    slots = []
    for row in rows:
        if row.get("wib_slot") not in slots:
            slots.append(row.get("wib_slot"))

    for slot in slots:
        slot_rows = [row for row in rows if row.get("wib_slot") == slot]
        parts.append(f"<h3>WIB {_e(slot)}</h3>")

        positions = []
        for row in slot_rows:
            if row.get("femb_position") not in positions:
                positions.append(row.get("femb_position"))

        summary = []
        for position in positions:
            femb_rows = [row for row in slot_rows if row.get("femb_position") == position]
            failed = [row for row in femb_rows if row.get("result") == "FAIL"]
            summary.append([position if position is not None else "-",
                            femb_rows[0].get("femb_serial") or "",
                            _verdict(not failed),
                            f"{len(femb_rows) - len(failed)} of {len(femb_rows)}"])
        parts.append(_table(["FEMB", "FEMB serial", "Result", "Checks passed"], summary))

        failed_rows = [row for row in slot_rows if row.get("result") == "FAIL"]
        if failed_rows:
            parts.append(_measurement_table(failed_rows, places))
        parts.append(f"<details><summary>All {len(slot_rows)} checks for WIB {_e(slot)}</summary>"
                     f"{_measurement_table(slot_rows, places)}</details>")
    return "".join(parts)


# ----------------------------------------------------------------------
def build_html(directory, config, item_names=None):
    data = _read_json(directory, config)
    places = config.get("rounding_factor")
    names = dict(data.get("item_names") or {})
    names.update(item_names or {})

    measurements = data.get("measurements", [])
    item_results = data.get("Items", {})
    overall = data.get("overall")

    # Item keys in the order they ran, then any item that did not run
    items = list(item_results) + [key for key in names if key not in item_results]

    # Measurement test names in the order they were first recorded
    tests = []
    for row in measurements:
        if row["test"] not in tests:
            tests.append(row["test"])

    def rows_for(test):
        return [row for row in measurements if row["test"] == test]

    def tests_of(item):
        return [item] + TEST_GROUPS.get(item, [])

    def title(key):
        return names.get(key) or SECTION_NAMES.get(key) or key

    overall_text = str(overall).upper() if overall else "NOT RECORDED"

    parts = ['<div class="wrap">']
    parts.append(f'<h1>WIEC {_e(data.get("wiec_serial", "unknown"))} {_verdict_span(overall_text)}</h1>')
    parts.append(f'<p class="sub">Run {_e(data.get("run_id", ""))}</p>')

    # -- headline facts
    graded = [row for row in measurements if row.get("result") in ("PASS", "FAIL")]
    failed = [row for row in graded if row["result"] == "FAIL"]
    duration = str(data.get("test_time", "")).split(".")[0]
    parts.append('<div class="facts">'
                 + _fact("Started", data.get("session_stamp", ""))
                 + _fact("Duration", duration)
                 + _fact("Tester", data.get("username", ""))
                 + _fact("Checks passed", f"{len(graded) - len(failed)} / {len(graded)}")
                 + "</div>")

    # -- one line per item
    parts.append("<h2>Items</h2>")
    item_rows = []
    for item in items:
        verdict = _verdict(None if item not in item_results else item_results[item] == "Pass")
        item_graded = [row for test in tests_of(item) for row in rows_for(test)
                       if row.get("result") in ("PASS", "FAIL")]
        item_failed = [row for row in item_graded if row["result"] == "FAIL"]
        item_rows.append([title(item), verdict, len(item_graded), len(item_failed)])
    parts.append(_table(["Item", "Result", "Checks", "Failed"], item_rows, numeric=(2, 3)))

    # -- why it failed
    parts.append("<h2>Why it failed</h2>")
    if failed:
        failed_rows = [[title(row["test"])] + [_number(row.get(key), places) for key, _ in ROW_COLUMNS
                                               if key not in ("result", "setpoint")]
                       for row in failed]
        headers = ["Test"] + [t for key, t in ROW_COLUMNS if key not in ("result", "setpoint")]
        parts.append(_fold(f"{len(failed)} failed checks", _table(headers, failed_rows), len(failed)))
    elif overall and str(overall).lower() == "fail":
        parts.append('<div class="note">No failed checks were recorded. See the session log.</div>')
    else:
        parts.append('<div class="note">Every recorded check passed.</div>')

    # -- detail for every item, with the tests recorded inside it
    shown = []
    for item in items:
        verdict = _verdict(None if item not in item_results else item_results[item] == "Pass")
        parts.append(f'<h2>{_e(title(item))} {_verdict_span(verdict)}</h2>')
        has_content = False
        for test in tests_of(item):
            shown.append(test)
            rows = rows_for(test)
            figures = _figures(directory, config, test)
            if not rows and not figures:
                continue
            has_content = True
            if test != item:
                parts.append(f"<h3>{_e(title(test))}</h3>")
            if test == "femb_checkout":
                parts.append(_femb_section(rows, places))
            elif rows:
                parts.append(_fold(f"All {len(rows)} measurements", _measurement_table(rows, places), len(rows)))
            parts.append(figures)
        if not has_content:
            parts.append('<div class="note">No measurements recorded.</div>')

    # -- anything recorded under a test name that no item claims
    for test in tests:
        if test not in shown:
            rows = rows_for(test)
            parts.append(f"<h2>{_e(title(test))}</h2>")
            parts.append(_fold(f"All {len(rows)} measurements", _measurement_table(rows, places), len(rows)))
            parts.append(_figures(directory, config, test))

    # -- links to the files next to this summary (they work when the run folder is opened in place)
    links = []
    for key in ("wiec_session_log_name", "wiec_results_csv_name", "wiec_run_json_name"):
        name = config.get(key)
        if name and os.path.isfile(os.path.join(directory, name)):
            links.append(f'<a href="{_e(name)}">{_e(name)}</a>')
    parts.append(
        "<footer>"
        + ("Files: " + " · ".join(links) + "<br>" if links else "")
        + f"Summary generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        + "</footer></div>"
    )

    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>WIEC {_e(data.get('wiec_serial', ''))} {_e(overall_text)}</title>"
        f"<style>{ROOT_CSS}{CSS}</style></head><body>"
        + "".join(parts)
        + "</body></html>"
    )


def write_report(directory, config, item_names=None):
    """Writes the summary into the run folder and returns its path.
    item_names maps item KEY -> NAME (e.g. {item.KEY: item.NAME for item in CRATE_ITEMS})."""
    path = os.path.join(directory, config["wiec_report_name"])
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(build_html(directory, config, item_names))
    return path


# ----------------------------------------------------------------------
def _run_directories(config):
    runs = os.path.join(config["WIEC_results_folder"], "runs")
    return sorted((d for d in glob.glob(os.path.join(runs, "*"))
                   if os.path.isfile(os.path.join(d, config["wiec_run_json_name"]))),
                  key=os.path.getmtime)


def main(argv):
    argv = argv[1:]
    config = load_config()

    if "--all" in argv:
        directories = _run_directories(config)
    elif argv:
        directories = argv
    else:
        directories = _run_directories(config)[-1:]

    if not directories:
        print(f"No run folders with {config['wiec_run_json_name']} under {config['WIEC_results_folder']}")
        return 1

    failures = 0
    for directory in directories:
        try:
            print("wrote", write_report(directory, config))
        except Exception as exc:
            failures += 1
            print(f"{directory}: {type(exc).__name__}: {exc}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
