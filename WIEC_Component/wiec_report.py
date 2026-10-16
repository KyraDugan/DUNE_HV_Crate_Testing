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

Graded results are always shown. Every test's plots are in boxes, wiec_report_plot_columns
across; a graded row that names its plot (the "plot" field from record_results.measure) is in a
table above that plot, and the rest of a test's graded rows are in one table above its plots.
Rows with no PASS/FAIL are folded into one "Not graded" dropdown per test, the only thing on the
page that is folded away.

A test whose plots are all one per channel (named <test type>_ch<N>.png, as hv_test's are) gets
one heading and one grid per test type. FEMB checkout boxes each FEMB's result line with its
pulse plot (wib<slot>/FEMB<serial>_.../*.png).

-------------------------------------------------------------------------------------------------------
config.json keys used:

"WIEC_results_folder"                                Folder holding runs/<run id>/ (for --all / newest)
"wiec_run_json_name"                                 Name of the run JSON in each run folder
"wiec_plots_folder"                                  Name of the plots folder in each run folder
"wiec_results_csv_name"                              Name of the measurements CSV (linked in the footer)
"wiec_session_log_name"                              Name of the session log (linked in the footer)
"wiec_report_name"                                   Name of the HTML summary written into each run folder
"wiec_report_plot_columns"                           Plot boxes across the page, per test (plots folder
                                                     name, e.g. "fan_pwm_sweep"); "default" for the rest
"rounding_factor"                                    Decimal places shown for measured values
-------------------------------------------------------------------------------------------------------
"""

import base64
import colorsys
import glob
import html
import json
import os
import re
import struct
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

# A plot named <test type>_ch<N>.png is channel N of that test type
CHANNEL_PLOT = re.compile(r"^(?P<group>.+)_ch(?P<channel>\d+)\.png$")

# hv_test's test types end in <polarity>_<termination>_<on/off>, e.g. "pos_open_on"
HV_PHASE = re.compile(r"(pos|neg)_(open|term)_(on|off)$")


def _pale(hex_color):
    """CSS colour for a pale cell fill, the same shade as the QC sheet's cells."""
    return "#" + WIEC_utils.cell_rgb(hex_color, WIEC_utils.CELL_OPACITY)[2:]


def _desaturated(hex_color, saturation=0.3, lightness=0.82):
    """CSS colour with hex_color's hue but greyer and lighter, so black text on it stays readable."""
    hue, _, _ = colorsys.rgb_to_hls(*(int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)))
    return "#" + "".join(f"{round(c * 255):02X}" for c in colorsys.hls_to_rgb(hue, lightness, saturation))


# Every colour is a WIEC_utils scheme colour (full, faded towards white or desaturated), white,
# or black at an opacity
ROOT_CSS = (f":root {{ --pass: {WIEC_utils.PASS_HEX}; --fail: {WIEC_utils.FAIL_HEX}; "
            f"--pass-fill: {_pale(WIEC_utils.PASS_HEX)}; --fail-fill: {_pale(WIEC_utils.FAIL_HEX)}; "
            f"--na-fill: {_pale(WIEC_utils.BAR_EMPTY_HEX)}; --head: {_desaturated(WIEC_utils.ACTION_HEX)}; "
            f"--interrupted-fill: {_pale(WIEC_utils.IN_PALETTE)}; "
            f"--title: {WIEC_utils.BANNER_BACKGROUND_HEX}; --banner-text: {WIEC_utils.BANNER_TEXT_HEX}; "
            f"--header: {WIEC_utils.HEADER_HEX}; --link: {WIEC_utils.ACTION_HEX}; }}")

CSS = """
:root { --bg: #ffffff; --fg: #000000; --muted: rgba(0, 0, 0, .6); --line: rgba(0, 0, 0, .12); }
* { box-sizing: border-box; }
body { margin: 0; padding: 2rem 1.5rem 4rem; background: var(--bg); color: var(--fg);
       font: 15px/1.55 -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif; }
.wrap { max-width: 1500px; margin: 0 auto; }
h1 { font-size: 2.3rem; margin: 0 0 .4rem; color: var(--title); }
/* Headings copy the terminal (WIEC_utils): an item's heading is print_banner's bar (banner text
   on the banner background, here on the left) and a part of an item is print_prompt_section's
   "── Title ─────" in the header colour, both in the terminal's monospace font */
h2, h3 { font-family: "DejaVu Sans Mono", "Ubuntu Mono", Menlo, Consolas, monospace; }
h2 { font-size: 1.2rem; margin: 3rem 0 1.2rem; padding: 1.1rem 1.2rem; text-align: left;
     background: var(--title); color: var(--banner-text); }
h3 { display: flex; align-items: center; gap: .6rem; font-size: 1.05rem; margin: 2rem 0 .8rem;
     color: var(--header); }
h3::before { content: ""; flex: 0 0 1.6rem; border-top: 2px solid var(--header); }
h3::after { content: ""; flex: 1; border-top: 2px solid var(--header); }
a { color: var(--link); }
/* Every row in the Items table links to its item's section; the section it lands on is outlined */
html { scroll-behavior: smooth; }
a.jump { font-weight: 700; }
tr.jump-row { cursor: pointer; }
tr.jump-row:hover { outline: 2px solid var(--header); outline-offset: -2px; }
h2 { scroll-margin-top: 1rem; }
h2:target { outline: 4px solid var(--header); outline-offset: 3px; }
/* Back to the top, always in the window's corner (left out of a printout) */
a.to-top { position: fixed; right: 1.2rem; bottom: 1.2rem; z-index: 10; padding: .6rem 1rem;
           background: var(--title); color: var(--banner-text); font-weight: 700;
           text-decoration: none; border-radius: 6px; box-shadow: 0 2px 8px rgba(0, 0, 0, .3);
           font-family: "DejaVu Sans Mono", "Ubuntu Mono", Menlo, Consolas, monospace; }
a.to-top:hover { background: var(--header); }
@media print { a.to-top { display: none; } }
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
/* Gold text would be too faint on the pale gold, so stopped items are bold black on it */
.INTERRUPTED { color: var(--fg); background: var(--interrupted-fill); font-weight: 700; }
.file { color: var(--muted); font-size: .75rem; word-break: break-all; margin-top: .2rem; }
/* Every plot on the page is in a box with its table above it, wiec_report_plot_columns across
   (see _grid_css). The box edge is thick and in the header colour so it is clear which table goes
   with which plot */
.plot-grid { display: grid; gap: 2.4rem; margin: 1.2rem 0 2rem; }
.cell { border: 2.5px solid var(--header); padding: .8rem 1rem 1rem; }
.cell.failed { border-color: var(--fail); border-left-width: 8px; }
.cell h4 { font-size: 1.2rem; margin: 0 0 .8rem; }
.cell .scroll { margin-bottom: .8rem; }
.cell img { width: 100%; height: auto; border: 1px solid var(--line); }
/* A test type's heading (one per hv_test phase): the header style, bigger and with a heavier
   rule and more space above, so it is hard to miss when scrolling */
h3.group { font-size: 1.25rem; margin-top: 3.5rem; }
h3.group::before, h3.group::after { border-top-width: 4px; }
details.ungraded { border: 1px solid var(--line); margin: 1rem 0; }
details.ungraded summary { cursor: pointer; padding: .5rem .8rem; color: var(--muted);
                           font-weight: 600; background: var(--na-fill); }
details.ungraded[open] summary { border-bottom: 1px solid var(--line); }
.note { border-left: 3px solid var(--line); padding: .5rem .9rem; color: var(--muted);
        margin: 1rem 0; font-size: .9rem; }
footer { margin-top: 3rem; color: var(--muted); font-size: .82rem; }
"""


def _columns(config, test):
    """How many plot boxes go across for this test: its entry in wiec_report_plot_columns, else
    that setting's "default"."""
    setting = config["wiec_report_plot_columns"]
    return int(setting.get(test, setting["default"]))


def _plot_grid(cells, columns):
    """Plot boxes, `columns` across (the cols-<N> rules come from _grid_css)."""
    return f'<div class="plot-grid cols-{columns}">{cells}</div>'


def _grid_css(config):
    """A cols-<N> rule for every column count in wiec_report_plot_columns: N across, dropping to
    at most 2 on a narrow window and 1 on a phone-sized one (the media rules come last so they win)."""
    counts = sorted({int(n) for n in config["wiec_report_plot_columns"].values()})
    rules = [f".plot-grid.cols-{n} {{ grid-template-columns: repeat({n}, minmax(0, 1fr)); }}"
             for n in counts]
    rules += [f"@media (max-width: 1100px) {{ .plot-grid.cols-{n} {{ grid-template-columns: "
              f"repeat({min(n, 2)}, minmax(0, 1fr)); }} }}" for n in counts]
    rules += [f"@media (max-width: 700px) {{ .plot-grid.cols-{n} {{ grid-template-columns: 1fr; }} }}"
              for n in counts]
    return "".join(rules)


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


def _png_size(path):
    """(width, height) in pixels from a PNG's header (bytes 16-24 of the IHDR chunk), or None
    if the file is not a PNG."""
    with open(path, "rb") as handle:
        header = handle.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", header[16:24])


def _e(value):
    return html.escape("" if value is None else str(value))


def _fact(key, value):
    return f'<div class="fact"><div class="k">{_e(key)}</div><div class="v">{_e(value)}</div></div>'


class _Html(str):
    """A table cell that is already HTML (e.g. a link), so _table does not escape it."""


def _item_anchor(item):
    """The id of an item's heading, so the Items table can link to it."""
    return f"item-{item}"


def _table(headers, rows, numeric=(), row_anchors=None):
    """row_anchors: one heading id (or None) per row; a row with one jumps there when clicked."""
    out = ['<div class="scroll"><table><thead><tr>']
    out += [f"<th>{_e(h)}</th>" for h in headers]
    out.append("</tr></thead><tbody>")
    for number, row in enumerate(rows):
        anchor = row_anchors[number] if row_anchors else None
        if anchor:
            # Setting the hash outlines the heading (h2:target); scrollIntoView scrolls even when
            # the hash is already set, e.g. on a second click
            out.append(f'<tr class="jump-row" onclick="location.hash = \'{_e(anchor)}\'; '
                       f'document.getElementById(\'{_e(anchor)}\').scrollIntoView()">')
        else:
            out.append("<tr>")
        for index, cell in enumerate(row):
            css = ' class="num"' if index in numeric else ""
            if cell in ("PASS", "FAIL", "INTERRUPTED", "not tested"):
                css = f' class="{"NA" if cell == "not tested" else cell}"'
            out.append(f"<td{css}>{cell if isinstance(cell, _Html) else _e(cell)}</td>")
        out.append("</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def _number(value, places):
    if isinstance(value, float) and places is not None:
        return round(value, places)
    return value


def _measurement_table(rows, places, skip=()):
    """Table of measurement rows. Columns that are empty in every row are left out, and so are
    the keys in skip."""
    columns = [(key, title) for key, title in ROW_COLUMNS
               if key not in skip and any(row.get(key) not in (None, "") for row in rows)]
    numeric = [i for i, (key, _) in enumerate(columns) if key in ("value", "min", "max", "setpoint")]
    table_rows = [[_number(row.get(key), places) for key, _ in columns] for row in rows]
    return _table([title for _, title in columns], table_rows, numeric=numeric)


def _plot_title(name, rows):
    """A plot's heading: the plot_title its rows recorded, else the file name."""
    return next((row["plot_title"] for row in rows if row.get("plot_title")), None) or name


def _verdict(passed):
    if passed is None:
        return "not tested"
    return "PASS" if passed else "FAIL"


def _verdict_span(text):
    """PASS / FAIL / anything else, in the same colours as the table cells."""
    css = text if text in ("PASS", "FAIL", "INTERRUPTED") else "NA"
    return f'<span class="verdict {css}">{_e(text)}</span>'


def _channel_groups(path_of):
    """{test type: [plot names, in channel order]} when every plot is one channel of a test type
    (named <test type>_ch<N>.png), else None. hv_test's types are ordered open then terminated,
    ramp up then down, positive then negative."""
    matches = {name: CHANNEL_PLOT.match(name) for name in path_of}
    if not matches or not all(matches.values()):
        return None

    groups = {}
    for name, match in sorted(matches.items(), key=lambda item: int(item[1]["channel"])):
        groups.setdefault(match["group"], []).append(name)

    def order(group):
        phase = HV_PHASE.search(group)
        if not phase:
            return (1, group)
        # Open before terminated, ramp up before ramp down, then positive before negative, so each
        # positive section sits next to its negative twin
        polarity, termination, edge = phase.groups()
        return (0, ("open", "term").index(termination), ("on", "off").index(edge),
                ("pos", "neg").index(polarity))

    return {group: groups[group] for group in sorted(groups, key=order)}


def _group_title(group):
    """A test type's heading: hv_test's phase in words, else the test type as named in the file."""
    phase = HV_PHASE.search(group)
    if not phase:
        return group
    polarity, termination, edge = phase.groups()
    return (f'{"Positive" if polarity == "pos" else "Negative"} channels, '
            f'{"open termination" if termination == "open" else "termination resistor"}, '
            f'ramp {"up" if edge == "on" else "down"}')


def _shared_ending(titles):
    """The words every title ends with: "from 0 to 2000V, open termination" for
    "Ch 0+ from 0 to 2000V, open termination" and "Ch 1+ from 0 to 2000V, open termination"."""
    shared = []
    for words in zip(*(reversed(title.split(" ")) for title in titles)):
        if len(set(words)) != 1:
            break
        shared.insert(0, words[0])
    return " ".join(shared)


def _cell(path, name, label, table, verdict=None, failed=False):
    """One box in a plot grid: the heading (label and verdict, left out when both are empty), the
    table that goes with the plot, then the plot and its file name. path None: a box with no plot."""
    verdict_html = _verdict_span(verdict) if verdict else ""
    heading = f"<h4>{_e(label)} {verdict_html}</h4>" if label or verdict else ""
    plot = ""
    if path:
        # The plot's real size, so the page saves room for it before it is drawn: otherwise the
        # page grows as plots appear, and a jump from the Items table stops short of its section.
        # (No loading="lazy" for the same reason; the plots are inside the file anyway.)
        size = _png_size(path)
        size_attrs = f' width="{size[0]}" height="{size[1]}"' if size else ""
        plot = (f'<img alt="{_e(name)}"{size_attrs} src="data:image/png;base64,{_embed(path)}">'
                f'<div class="file">{_e(name)}</div>')
    return f'<div class="cell{" failed" if failed else ""}">{heading}{table}{plot}</div>'


def _rows_cell(path, name, label, rows, places):
    """A plot's box with the measurement rows that name it as its table."""
    failed = any(row.get("result") == "FAIL" for row in rows)
    graded = any(row.get("result") in ("PASS", "FAIL") for row in rows)
    # The heading already names the channel, so the table leaves it out
    table = _measurement_table(rows, places, skip=("channel",)) if rows else ""
    return _cell(path, name, label, table, ("FAIL" if failed else "PASS") if graded else None, failed)


def _hv_label(group, name, params):
    """An hv_test box's heading, e.g. "Ch 0 open, 0 -> -2000V", from the run's settings
    (input_params in run.json), so the not-graded plots are labelled too. The file is named by
    CAEN channel; pcb_ch_<N>_<polarity> maps that back to the board's channel. None if the run
    did not record what is needed."""
    phase = HV_PHASE.search(group)
    caen = int(CHANNEL_PLOT.match(name)["channel"])
    polarity, termination, edge = phase.groups()
    board = [n for n in range(16) if params.get(f"pcb_ch_{n}_{polarity}") == caen]
    volts = params.get(f"caenR8033DM_{termination}_voltage")
    if not board or volts is None:
        return None
    volts = f'{"-" if polarity == "neg" else ""}{volts:g}'
    span = f"0 -> {volts}V" if edge == "on" else f"{volts} -> 0V"
    return f'Ch {board[0]} {"open" if termination == "open" else "terminated"}, {span}'


def _group_grid(group, names, path_of, rows_of, places, params, columns):
    """One test type: a heading (h3.group) with its overall result, then its channels in the plot grid."""
    group_rows = [row for name in names for row in rows_of.get(name, [])]
    if any(row["result"] == "FAIL" for row in group_rows):
        verdict = "FAIL"
    else:
        verdict = "PASS" if group_rows else "not graded"

    # hv_test boxes are labelled from the run's settings. Otherwise each plot's title is
    # "Ch 0+ from 0 to 2000V, ..."; the heading says the shared part, so a box is labelled by what
    # is left ("Ch 0+"). A plot with no rows has its title drawn on it.
    titles = {name: _plot_title(name, rows_of[name]) for name in names if name in rows_of}
    ending = _shared_ending(titles.values()) if len(titles) > 1 else ""
    labels = {name: title[:len(title) - len(ending)].strip() if ending else title
              for name, title in titles.items()}
    if HV_PHASE.search(group):
        for name in names:
            labels[name] = _hv_label(group, name, params) or labels.get(name, "")

    cells = "".join(_rows_cell(path_of[name], name, labels.get(name, ""), rows_of.get(name, []), places)
                    for name in names)
    return (f'<h3 class="group">{_e(_group_title(group))} {_verdict_span(verdict)}</h3>'
            + _plot_grid(cells, columns))


def _test_section(directory, config, test, rows, places, params=None):
    """A test's rows and plots. Plots go in the plot grid, each boxed with the graded rows
    (PASS/FAIL) that name it (their "plot" field) in a table above it, in the order the rows were
    recorded; plots no graded row names come after, with no table. The other graded rows go in one
    table above the grid, and rows with no grade in a closed "Not graded" dropdown under that table.
    Plots that are all one per channel get one grid per test type (see _channel_groups); params is
    the run's input_params, used for the hv_test box headings."""
    pattern = os.path.join(directory, config["wiec_plots_folder"], test, "**", "*.png")
    plot_root = os.path.join(directory, config["wiec_plots_folder"], test)
    path_of = {os.path.relpath(path, plot_root): path
               for path in sorted(glob.glob(pattern, recursive=True))}

    # Readings with no PASS/FAIL (recorded for reference only) go in one dropdown per test,
    # so the graded results are what the section shows first
    ungraded = [row for row in rows if row.get("result") not in ("PASS", "FAIL")]
    graded = [row for row in rows if row.get("result") in ("PASS", "FAIL")]

    rows_of = {}
    table_rows = []
    for row in graded:
        if row.get("plot") in path_of:
            rows_of.setdefault(row["plot"], []).append(row)
        else:
            table_rows.append(row)

    parts = [_measurement_table(table_rows, places)] if table_rows else []
    if ungraded:
        parts.append(f'<details class="ungraded"><summary>Not graded: {len(ungraded)} '
                     f'reading{"s" if len(ungraded) != 1 else ""} recorded for reference, '
                     f'no pass/fail limit</summary>{_measurement_table(ungraded, places)}</details>')
    columns = _columns(config, test)
    groups = _channel_groups(path_of)
    if groups:
        parts += [_group_grid(group, names, path_of, rows_of, places, params or {}, columns)
                  for group, names in groups.items()]
    elif path_of:
        names = list(rows_of) + [name for name in path_of if name not in rows_of]
        cells = "".join(_rows_cell(path_of[name], name,
                                   _plot_title(name, rows_of[name]) if name in rows_of else "",
                                   rows_of.get(name, []), places)
                        for name in names)
        parts.append(_plot_grid(cells, columns))
    return "".join(parts)


def _femb_plot(path_of, slot, serial):
    """The pulse plot BNL's checkout made for one FEMB: wib<slot>/FEMB<serial>_.../*.png, or None."""
    for name in path_of:
        folders = name.split(os.sep)
        if len(folders) > 2 and folders[0] == f"wib{slot}" and folders[1].startswith(f"FEMB{serial}_"):
            return name
    return None


def _femb_section(directory, config, rows, places):
    """FEMB checkout: one block per WIB slot, with a box per FEMB in the plot grid. A box's table
    is that FEMB's line (result and how many checks passed), then any checks it failed, above its
    pulse plot. Every check is in the measurements CSV; only the failed ones are listed here.
    Plots that match no FEMB come after, with no table."""
    plot_root = os.path.join(directory, config["wiec_plots_folder"], "femb_checkout")
    path_of = {os.path.relpath(path, plot_root): path
               for path in sorted(glob.glob(os.path.join(plot_root, "**", "*.png"), recursive=True))}
    columns = _columns(config, "femb_checkout")
    shown = []

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

        cells = []
        for position in positions:
            femb_rows = [row for row in slot_rows if row.get("femb_position") == position]
            failed = [row for row in femb_rows if row.get("result") == "FAIL"]
            serial = femb_rows[0].get("femb_serial") or ""
            table = _table(["FEMB", "FEMB serial", "Result", "Checks passed"],
                           [[position if position is not None else "-", serial, _verdict(not failed),
                             f"{len(femb_rows) - len(failed)} of {len(femb_rows)}"]])
            if failed:
                table += _measurement_table(failed, places, skip=("wib_slot", "femb_position", "femb_serial"))
            name = _femb_plot(path_of, slot, serial) if serial else None
            shown.append(name)
            cells.append(_cell(path_of.get(name), name,
                               f"FEMB {position if position is not None else '-'}",
                               table, _verdict(not failed), bool(failed)))
        parts.append(_plot_grid("".join(cells), columns))

    # Plots no FEMB's rows point at (e.g. an FEMB whose checks were not recorded)
    others = [name for name in path_of if name not in shown]
    if others:
        parts.append(_plot_grid("".join(_cell(path_of[name], name, "", "") for name in others), columns))
    return "".join(parts)


# ----------------------------------------------------------------------
def build_html(directory, config, item_names=None):
    data = _read_json(directory, config)
    places = config.get("rounding_factor")
    # The settings the run used (hv_test's channel map and voltages label its plots)
    params = data.get("input_params") or {}
    names = dict(data.get("item_names") or {})
    names.update(item_names or {})

    measurements = data.get("measurements", [])
    item_results = data.get("Items", {})
    overall = data.get("overall")
    # Items stopped partway through (Ctrl-C or an error), from run_wiec_component.run_item
    interrupted = data.get("interrupted_items", [])

    # Item keys in the order they ran (a stopped one has no result), then any item that did not run
    items = list(item_results) + [key for key in interrupted if key not in item_results]
    items += [key for key in names if key not in items]

    def item_verdict(item):
        if item in interrupted:
            return "INTERRUPTED"
        return _verdict(None if item not in item_results else item_results[item] == "Pass")

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

    def has_plots(test):
        return bool(glob.glob(os.path.join(directory, config["wiec_plots_folder"], test, "**", "*.png"),
                              recursive=True))

    overall_text = str(overall).upper() if overall else "NOT RECORDED"
    # Same rule as the QC sheet: a failed item outranks a stopped one
    verdicts = [item_verdict(item) for item in items]
    if "INTERRUPTED" in verdicts and "FAIL" not in verdicts:
        overall_text = "INTERRUPTED"

    # "#" scrolls to the top of the page and clears the outline on the section last jumped to
    parts = ['<a class="to-top" href="#" title="Back to the top">↑ Top</a><div class="wrap">']
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
    item_anchors = []
    for item in items:
        verdict = item_verdict(item)
        item_graded = [row for test in tests_of(item) for row in rows_for(test)
                       if row.get("result") in ("PASS", "FAIL")]
        item_failed = [row for row in item_graded if row["result"] == "FAIL"]
        # Every item's row (and its name, as a real link) jumps down to its section, where the
        # details are
        anchor = _item_anchor(item)
        name = _Html(f'<a class="jump" href="#{_e(anchor)}">{_e(title(item))} ↓</a>')
        item_rows.append([name, verdict, len(item_graded), len(item_failed)])
        item_anchors.append(anchor)
    parts.append(_table(["Item", "Result", "Checks", "Failed"], item_rows, numeric=(2, 3),
                        row_anchors=item_anchors))

    # -- detail for every item, with the tests recorded inside it
    shown = []
    for item in items:
        verdict = item_verdict(item)
        parts.append(f'<h2 id="{_e(_item_anchor(item))}">{_e(title(item))} {_verdict_span(verdict)}</h2>')
        if verdict == "INTERRUPTED":
            parts.append('<div class="note">Interrupted partway through (Ctrl-C or an error). '
                         'The results below are only what was recorded before it stopped.</div>')
        has_content = False
        for test in tests_of(item):
            shown.append(test)
            rows = rows_for(test)
            if not rows and not has_plots(test):
                continue
            has_content = True
            if test != item:
                parts.append(f"<h3>{_e(title(test))}</h3>")
            if test == "femb_checkout":
                parts.append(_femb_section(directory, config, rows, places))
            else:
                parts.append(_test_section(directory, config, test, rows, places, params))
        if not has_content:
            parts.append('<div class="note">No measurements recorded.</div>')

    # -- anything recorded under a test name that no item claims
    for test in tests:
        if test not in shown:
            parts.append(f"<h2>{_e(title(test))}</h2>")
            parts.append(_test_section(directory, config, test, rows_for(test), places, params))

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
        f"<style>{ROOT_CSS}{CSS}{_grid_css(config)}</style></head><body>"
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
