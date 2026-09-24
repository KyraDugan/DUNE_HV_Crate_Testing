"""Turns a finished hv_module_test results directory into one HTML report.

    python3 -m WIEC_Component.hv_module_report                    newest run
    python3 -m WIEC_Component.hv_module_report <results dir>
    python3 -m WIEC_Component.hv_module_report --all              every run

hv_module_test calls this itself when a run finishes, so the report is already
sitting next to the data; the command line is for rebuilding an old one after
the template changes.

The HTML is **self-contained** -- the plots are embedded as base64, so the file
can be mailed or dropped on a share and still render.  It reads only what the
run wrote:

    hv_module_test_SN*.json          every step, plus the measurements
    hv_module_test_SN*_results.csv   the same steps as a flat table
    hv_ramp_SN*.csv                  the per-channel ramp samples
    hv_ramp_daq_SN*.csv              the 34970A readings, when it was wired
    hv_iv_SN*.csv                    the averaged I-V points
    *.png                            the curves
"""

import base64
import csv
import glob
import html
import json
import os
import sys
from datetime import datetime

RESULTS_ROOT = os.path.join("HV Module Test", "results")

CSS = """
:root {
  --bg: #ffffff; --fg: #1a1a1a; --muted: #666; --line: #e0e0e0;
  --pass: #0a7c2f; --fail: #c62828; --na: #888; --head: #f5f5f5;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #16181a; --fg: #e6e6e6; --muted: #a0a0a0; --line: #333;
    --pass: #4caf50; --fail: #ef5350; --na: #999; --head: #1f2225;
  }
}
* { box-sizing: border-box; }
body { margin: 0; padding: 2rem 1.5rem 4rem; background: var(--bg); color: var(--fg);
       font: 15px/1.55 -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif; }
.wrap { max-width: 1100px; margin: 0 auto; }
h1 { font-size: 1.55rem; margin: 0 0 .3rem; }
h2 { font-size: 1.15rem; margin: 2.4rem 0 .8rem; padding-bottom: .35rem;
     border-bottom: 1px solid var(--line); }
.sub { color: var(--muted); margin: 0 0 1.4rem; }
.badge { display: inline-block; padding: .25rem .85rem; border-radius: 999px;
         font-weight: 700; letter-spacing: .04em; color: #fff; }
.badge.pass { background: var(--pass); } .badge.fail { background: var(--fail); }
.facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
         gap: .6rem; margin: 1.2rem 0; }
.fact { border: 1px solid var(--line); border-radius: 8px; padding: .6rem .8rem; }
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
.PASS { color: var(--pass); font-weight: 700; }
.FAIL { color: var(--fail); font-weight: 700; }
.NA   { color: var(--na); }
.kind { color: var(--muted); font-size: .8rem; }
figure { margin: 1.4rem 0; }
figure img { width: 100%; height: auto; border: 1px solid var(--line); border-radius: 8px; }
figcaption { color: var(--muted); font-size: .85rem; margin-top: .4rem; }
.note { border-left: 3px solid var(--line); padding: .5rem .9rem; color: var(--muted);
        margin: 1rem 0; font-size: .9rem; }
footer { margin-top: 3rem; color: var(--muted); font-size: .82rem; }
"""

PLOTS = [
    ("hv_ramp_voltage.png", "Ramp voltage, all channels, from the module's own measurement."),
    ("hv_ramp_current.png", "Ramp current, all channels."),
    ("hv_ramp_daq.png",
     "34970A through the HV divider. Recorded only — nothing is converted or "
     "graded, because the divider is 10M:500k (nominally 21.0) but measures ~44."),
    ("hv_iv.png",
     "I-V staircase: DC points after the dwell with the fit I = V/R + I0, the "
     "residuals against the linearity limit, and each channel's R_load against "
     "the median."),
]


# ----------------------------------------------------------------------
def _read_json(directory):
    for path in sorted(glob.glob(os.path.join(directory, "hv_module_test_*.json"))):
        with open(path) as handle:
            return json.load(handle), path
    return None, None


def _read_csv(directory, pattern):
    for path in sorted(glob.glob(os.path.join(directory, pattern))):
        with open(path, newline="") as handle:
            return list(csv.DictReader(handle)), path
    return [], None


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
            if cell in ("PASS", "FAIL", "N/A"):
                css = f' class="{"NA" if cell == "N/A" else cell}"'
            out.append(f"<td{css}>{_e(cell)}</td>")
        out.append("</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


# ----------------------------------------------------------------------
def build_html(directory):
    data, json_path = _read_json(directory)
    if data is None:
        raise FileNotFoundError(f"no hv_module_test_*.json in {directory}")

    module = data.get("module", {})
    measurements = data.get("measurements", {})
    steps = data.get("steps", [])
    overall = data.get("overall", "Fail")
    passed = str(overall).lower().startswith("pass")

    ramp_rows, ramp_csv = _read_csv(directory, "hv_ramp_SN*.csv")
    daq_rows, daq_csv = _read_csv(directory, "hv_ramp_daq_SN*.csv")
    iv_rows, iv_csv = _read_csv(directory, "hv_iv_SN*.csv")

    parts = ['<div class="wrap">']
    parts.append(
        f'<h1>HV Module Test — SN {_e(module.get("serial_number", "unknown"))} '
        f'<span class="badge {"pass" if passed else "fail"}">{_e(overall).upper()}</span></h1>'
    )
    parts.append(
        f'<p class="sub">{_e(module.get("model", ""))} '
        f'firmware {_e(module.get("firmware", ""))} '
        f'— slot {_e(data.get("slot"))} of the crate at {_e(data.get("crate_ip"))} '
        f'— {_e(data.get("timestamp"))}</p>'
    )

    if data.get("dry_run"):
        parts.append('<div class="note"><strong>Dry run.</strong> Simulated crate '
                     'and meter — these numbers are not measurements.</div>')

    # -- headline facts
    graded = [s for s in steps if s.get("result") in ("PASS", "FAIL")]
    failed = [s for s in graded if s["result"] == "FAIL"]
    seconds = measurements.get("ramp_seconds", {}) or {}
    rates = measurements.get("measured_ramp_rates", {}) or {}
    flat = [v for r in rates.values() for v in r.values()
            if isinstance(v, (int, float)) and v == v]

    facts = [
        _fact("Test name", data.get("test_name", "")),
        _fact("Steps passed", f"{len(graded) - len(failed)} / {len(graded)}"),
        _fact("Polarity", measurements.get("polarity", "")),
    ]
    if seconds:
        facts.append(_fact("Ramp up / hold / down",
                           " / ".join(f"{seconds.get(k, 0):.0f} s"
                                      for k in ("up", "hold", "down"))))
    if flat:
        facts.append(_fact("Measured ramp rate",
                           f"{min(flat):.2f} – {max(flat):.2f} V/s"))
    if measurements.get("ramp_rate_commanded_v_per_s"):
        facts.append(_fact("Commanded ramp rate",
                           f"{measurements['ramp_rate_commanded_v_per_s']:g} V/s"))
    if "hardware_limit_voltage_percent" in measurements:
        facts.append(_fact("Vmax pot",
                           f"{measurements['hardware_limit_voltage_percent']:.2f} %"))
        facts.append(_fact("Imax pot",
                           f"{measurements['hardware_limit_current_percent']:.2f} %"))
    parts.append(f'<div class="facts">{"".join(facts)}</div>')

    if failed:
        parts.append("<h2>Failed steps</h2>")
        parts.append(_table(
            ["Step", "Detail"],
            [[s["title"], s.get("detail", "")] for s in failed],
        ))

    # -- every step
    parts.append("<h2>All steps</h2>")
    parts.append(_table(
        ["#", "Kind", "Step", "Result", "Detail", "Time"],
        [[i, s.get("kind", ""), s.get("title", ""), s.get("result", ""),
          s.get("detail", ""), (s.get("timestamp") or "")[11:]]
         for i, s in enumerate(steps, start=1)],
        numeric=(0,),
    ))

    # -- per-channel ramp rates
    if rates:
        parts.append("<h2>Ramp rate, per channel</h2>")
        parts.append(_table(
            ["Channel", "Up [V/s]", "Down [V/s]"],
            [[ch.upper(), f"{r.get('up_v_per_s', float('nan')):.3f}",
              f"{r.get('down_v_per_s', float('nan')):.3f}"]
             for ch, r in sorted(rates.items())],
            numeric=(1, 2),
        ))

    # -- the I-V staircase
    iv = (measurements.get("iv_analysis") or {}).get("per_channel") or {}
    if iv:
        median = measurements["iv_analysis"].get("median_load_mohm", float("nan"))
        parts.append("<h2>I-V, per channel</h2>")
        parts.append(
            f'<div class="note">DC points {_e(", ".join(f"{v:g}" for v in measurements.get("iv_voltages", [])))} V. '
            f'Linearity is the worst residual from the fitted line as a share of '
            f'the top-point current; deviation is R_load against the median '
            f'{median:.3f} MΩ.</div>'
        )
        parts.append(_table(
            ["Channel", "R_load [MΩ]", "I0 [µA]", "I at top [µA]",
             "Linearity", "vs median"],
            [[a["channel"].upper(), f"{a['load_mohm']:.3f}", f"{a['offset_ua']:.3f}",
              f"{a['top_current_ua']:.2f}", f"{a['linearity_fraction']:.2%}",
              f"{a['load_deviation_fraction']:+.2%}"]
             for a in sorted(iv.values(), key=lambda a: a["channel"])],
            numeric=(1, 2, 3, 4, 5),
        ))

    # -- the DAQ observation
    observation = measurements.get("daq_observation")
    if observation:
        nominal = observation.get("nominal_divider_ratio")
        parts.append("<h2>34970A observation</h2>")
        parts.append(
            f'<div class="note">Recorded, not graded. The divider is nominally '
            f'{nominal:.1f} from 10M:500k; what the measurements imply is below. '
            f'Nothing in this run was converted through either number.</div>'
        )
        parts.append(_table(
            ["Channel", "Points", "Mean ratio", "Min", "Max"],
            [[d["channel"].upper(), d["points"], f"{d['mean_ratio']:.3f}",
              f"{d['min_ratio']:.3f}", f"{d['max_ratio']:.3f}"]
             for d in sorted(observation.get("per_channel", {}).values(),
                             key=lambda d: d["channel"])],
            numeric=(1, 2, 3, 4),
        ))

    # -- curves
    figures = [(name, caption) for name, caption in PLOTS
               if os.path.isfile(os.path.join(directory, name))]
    if figures:
        parts.append("<h2>Curves</h2>")
        for name, caption in figures:
            encoded = _embed(os.path.join(directory, name))
            parts.append(
                f'<figure><img alt="{_e(caption)}" '
                f'src="data:image/png;base64,{encoded}">'
                f'<figcaption>{caption}</figcaption></figure>'
            )

    counts = []
    if ramp_rows:
        counts.append(f"{len(ramp_rows)} ramp samples ({os.path.basename(ramp_csv)})")
    if daq_rows:
        counts.append(f"{len(daq_rows)} DAQ samples ({os.path.basename(daq_csv)})")
    if iv_rows:
        counts.append(f"{len(iv_rows)} I-V points ({os.path.basename(iv_csv)})")
    parts.append(
        "<footer>"
        + ("Data: " + "; ".join(counts) + ".<br>" if counts else "")
        + f"Source: {_e(os.path.basename(json_path))} in {_e(directory)}<br>"
        + f"Report generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        + "</footer></div>"
    )

    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>HV Module Test — SN {_e(module.get('serial_number', ''))}</title>"
        f"<style>{CSS}</style></head><body>"
        + "".join(parts)
        + "</body></html>"
    )


def write_report(directory, filename="report.html"):
    path = os.path.join(directory, filename)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(build_html(directory))
    return path


# ----------------------------------------------------------------------
def _run_directories():
    return sorted(d for d in glob.glob(os.path.join(RESULTS_ROOT, "*"))
                  if os.path.isdir(d))


def main(argv):
    argv = argv[1:]

    if "--all" in argv:
        directories = _run_directories()
    elif argv:
        directories = argv
    else:
        directories = _run_directories()[-1:]

    if not directories:
        print(f"No run directories under {RESULTS_ROOT}")
        return 1

    failures = 0
    for directory in directories:
        try:
            print("wrote", write_report(directory))
        except Exception as exc:
            failures += 1
            print(f"{directory}: {type(exc).__name__}: {exc}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
