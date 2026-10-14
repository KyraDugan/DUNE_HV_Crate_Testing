"""Curve fitting and plotting shared by the HV items."""


import csv
import os
from datetime import datetime
from pathlib import Path

import matplotlib

# These items only ever write PNGs, and they routinely run over ssh, so keep
# matplotlib off any display.
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import curve_fit

from . import WIEC_utils

# Plot colours, all from the WIEC_utils palette
DATA_HEX = WIEC_utils.BANNER_BACKGROUND_HEX    # the measured trace that is analysed (dark blue)
FIT_HEX = WIEC_utils.IN_PALETTE                # the fit / drift line drawn over it (gold)
OTHER_HEX = WIEC_utils.HEADER_HEX              # the quantity shown only for context
BAND_HEX = WIEC_utils.BAR_EMPTY_HEX            # +/- 1 sigma band, grid and neutral box edges


def exp_fit(x, a, b, c):
    return a * np.exp(-b * x) + c


def get_ch_data(data_file, ch):
    """Reads one CAEN channel from a recorded HV CSV.

    Returns (seconds since the first sample as a numpy array, voltages, currents).
    """
    stamps = []
    ch_voltage = []
    ch_current = []

    with open(data_file, "r", newline="") as csvfile:
        for row in csv.reader(csvfile, delimiter=","):
            # fromisoformat also reads a timestamp that landed on a whole second (no ".%f" part)
            stamps.append(datetime.fromisoformat(row[0]))
            ch_voltage.append(float(row[1 + (ch * 2)]))
            ch_current.append(float(row[2 + (ch * 2)]))

    seconds = np.array([(stamp - stamps[0]).total_seconds() for stamp in stamps])
    return seconds, ch_voltage, ch_current


def hv_curve_fit(results_path, name, ch, on=True, initial_b=0.02):
    """Fits a*exp(-b*t)+c to the ramp: the current after a ramp up (on), the voltage after a
    ramp down (off).

    Returns the raw scipy result (popt, pcov); fit[0][1] is b, the decay rate in 1/s, and the
    time constant is tau = 1/b.  A fit that does not converge comes back as [[0, 0]], which
    fails the threshold check rather than crashing the run.

    Added initial parameters to allow for better convergence.
    initial_b is included in config.json for adjustment while calling the function.
    """
    time_seconds, ch_voltage, ch_current = get_ch_data(
        os.path.join(results_path, name), ch
    )

    data = ch_current if on else ch_voltage

    try:
        p0=[data[0] - data[1],
            initial_b,
            data[-1]]
        return curve_fit(exp_fit, time_seconds, data, p0=p0)
    except RuntimeError:
        return [[0, 0]]


def steady_stats(seconds, values):
    """Describes a reading that is held steady: mean, noise, drift and peak-to-peak.

    The drift is a straight-line fit (always succeeds, unlike the exponential), and the noise
    is the standard deviation of what is left after taking that line away, so a slow drift
    does not count as noise. Returns a dict; drift is per minute, trend is the fitted line.
    """
    values = np.asarray(values, dtype=float)
    slope, intercept = np.polyfit(seconds, values, 1)
    trend = slope * seconds + intercept
    return {
        "mean": float(values.mean()),
        "sigma": float((values - trend).std()),
        "drift_per_min": float(slope * 60),
        "peak_to_peak": float(np.ptp(values)),
        "trend": trend,
    }


def _style(ax):
    ax.grid(True, color=BAND_HEX, alpha=0.5, linewidth=0.8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.tick_params(labelsize=12)
    # Show 1999.5 V as 1999.5, not as "-0.5" with a "+2e3" offset above the axis
    ax.ticklabel_format(axis="y", useOffset=False)


def _new_figure(title, height_ratios, verdict=None):
    """Two stacked panels sharing the time axis, with the title above them. verdict ("PASS" or
    "FAIL") is written right after the title in the program's pass/fail colour."""
    fig, axes = plt.subplots(
        2, 1, figsize=(12, 8), sharex=True, gridspec_kw=dict(height_ratios=height_ratios)
    )
    title_text = fig.text(0.06, 0.96, title, fontsize=18, ha="left", va="top")
    if verdict is not None:
        # A text object has one colour, so the verdict is a second text placed where the title ends
        title_box = title_text.get_window_extent(renderer=fig.canvas.get_renderer())
        title_end = fig.transFigure.inverted().transform((title_box.x1, title_box.y0))[0]
        fig.text(title_end + 0.012, 0.96, verdict, fontsize=18, fontweight="bold", ha="left", va="top",
                 color=WIEC_utils.PASS_HEX if verdict == "PASS" else WIEC_utils.FAIL_HEX)
    return fig, axes


def _save(fig, axes, filename, ch, results_path, plot_dir):
    axes[-1].set_xlabel("Time since recording started (s)", fontsize=13)
    fig.tight_layout(rect=[0, 0, 1, 0.92])
    png_name = f"{Path(filename).stem}_ch{ch}.png"
    fig.savefig(os.path.join(plot_dir or results_path, png_name), dpi=90)
    plt.close(fig)
    return png_name


def make_plot(results_path, filename, title, ch, fit=None, on=True, limits=None, other_ylim=None,
              plot_dir=None):
    """Plots one channel of a recorded HV CSV with its exponential fit.

    Top panel: the quantity that was fitted (current for a ramp up, voltage for a ramp down)
    with the fit drawn over it, and a box giving the time constant tau = 1/b and its limits.
    Bottom panel: the other quantity, for context. PASS/FAIL goes after the title.

    fit is hv_curve_fit's result; limits is (b_min, b_max) in 1/s, graded the same way as
    hv_test._evaluate (on b) and shown as the matching tau range. other_ylim is (low, high) for
    the bottom panel; it is only widened if the data goes outside it. The CSV is read from
    results_path; the PNG is saved in plot_dir if given, otherwise next to the CSV in
    results_path. Returns the PNG's file name.
    """
    seconds, ch_voltage, ch_current = get_ch_data(
        os.path.join(results_path, filename), ch
    )
    if on:
        fitted, fitted_label, other, other_label = ch_current, "Current (µA)", ch_voltage, "Voltage (V)"
    else:
        fitted, fitted_label, other, other_label = ch_voltage, "Voltage (V)", ch_current, "Current (µA)"
    fitted_name = fitted_label.split(" ")[0].lower()

    # A fit that failed comes back from hv_curve_fit as [[0, 0]], with no covariance
    fit_ok = fit is not None and len(fit) == 2 and len(fit[0]) == 3
    b = float(fit[0][1]) if fit_ok else 0.0
    if not fit_ok:
        lines = ["Fit failed"]
    elif b == 0:
        lines = ["τ: undefined (b = 0)"]
    else:
        lines = [f"τ = {1 / b:.1f} s"]

    verdict = None
    if limits is not None:
        b_min, b_max = limits
        verdict = "PASS" if b_min < b < b_max else "FAIL"
        # The limits are on b; a larger b is a shorter time constant, so they swap ends
        lines.append(f"Limit: {1 / b_max:.1f}–{1 / b_min:.1f} s")

    fig, (ax, ax_other) = _new_figure(title, height_ratios=[3, 1], verdict=verdict)

    # The CAEN holds each reading until its next update, so draw the data as steps.
    # zorder puts the data on top of the fit, which is drawn behind it.
    ax.step(seconds, fitted, where="post", color=DATA_HEX, linewidth=2, zorder=3,
            label=f"Measured {fitted_name}")
    if fit_ok:
        ax.plot(seconds, exp_fit(seconds, *fit[0]), color=FIT_HEX, linewidth=2.5, linestyle="--",
                zorder=2, label="Exponential fit")

    ax.text(0.98, 0.70, "\n".join(lines), transform=ax.transAxes, ha="right", va="top",
            multialignment="left", fontsize=13,
            bbox=dict(facecolor="white", edgecolor=BAND_HEX, linewidth=2))
    ax.set_ylabel(fitted_label, fontsize=13)
    ax.legend(loc="upper right", fontsize=11, frameon=False)
    _style(ax)

    ax_other.step(seconds, other, where="post", color=OTHER_HEX, linewidth=1.5)
    ax_other.set_ylabel(other_label, fontsize=11)
    _style(ax_other)
    if other_ylim is not None:
        # Fixed bounds, so the readback's small steps don't look like noise. If the data leaves
        # them (e.g. a trip) the panel widens to show it rather than clipping it off.
        ax_other.set_ylim(min(other_ylim[0], min(other)), max(other_ylim[1], max(other)))

    return _save(fig, (ax, ax_other), filename, ch, results_path, plot_dir)


def make_steady_plot(results_path, filename, title, ch, plot_dir=None):
    """Plots one channel of an HV CSV taken while the output is held steady (the terminated
    phases), where there is no decay to fit.

    Current and voltage each get a panel with the measured trace, a straight-line drift fit,
    a +/- 1 sigma band around it, and the steady_stats numbers written out. Saved like
    make_plot; returns the PNG's file name.
    """
    seconds, ch_voltage, ch_current = get_ch_data(
        os.path.join(results_path, filename), ch
    )

    # No verdict in the title: nothing on this plot is graded yet
    fig, axes = _new_figure(title, height_ratios=[1, 1])

    for ax, values, name, unit in ((axes[0], ch_current, "Current", "µA"),
                                   (axes[1], ch_voltage, "Voltage", "V")):
        stats = steady_stats(seconds, values)
        trend, sigma = stats["trend"], stats["sigma"]
        ax.fill_between(seconds, trend - sigma, trend + sigma, color=BAND_HEX, alpha=0.5,
                        linewidth=0, label="±1σ")
        ax.step(seconds, values, where="post", color=DATA_HEX, linewidth=2, zorder=3,
                label=f"Measured {name.lower()}")
        ax.plot(seconds, trend, color=FIT_HEX, linewidth=2, linestyle="--", zorder=2,
                label="Linear drift")
        ax.text(0.02, 0.95,
                f"mean {stats['mean']:.3f} {unit}   σ {sigma:.4f} {unit}   "
                f"drift {stats['drift_per_min']:+.3f} {unit}/min   p-p {stats['peak_to_peak']:.3f} {unit}",
                transform=ax.transAxes, va="top", fontsize=12,
                bbox=dict(facecolor="white", edgecolor=BAND_HEX))
        ax.set_ylabel(f"{name} ({unit})", fontsize=13)
        ax.legend(loc="lower right", fontsize=11, frameon=False, ncol=3)
        _style(ax)

    return _save(fig, axes, filename, ch, results_path, plot_dir)
