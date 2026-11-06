""" analysis.py

Curve fitting and plotting for the HV test.

For readability, makes sure the voltage and current are always the same color 
and the same placement within the subplots.

A lot of this file is formatting for the stacked plots.
"""

import csv, os, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from datetime import datetime
from pathlib import Path
from scipy.optimize import curve_fit
from . import WIEC_utils

# Plot colours, all from the WIEC_utils palette
CURRENT_HEX = WIEC_utils.ACTION_HEX            # every current trace, in every panel (teal)
VOLTAGE_HEX = WIEC_utils.BANNER_BACKGROUND_HEX # every voltage trace, in every panel (dark blue)
DATA_HEX = WIEC_utils.HEADER_HEX               # the resistance trace on the 20 V plots (purple)
FIT_HEX = WIEC_utils.IN_PALETTE                # the exponential fit, drawn in front of the data (gold)
GRADED_HEX = WIEC_utils.IN_PALETTE             # the star on the graded 20 V reading (gold)
BAND_HEX = WIEC_utils.BAR_EMPTY_HEX            # grid and the time-constant box's edge
POINT_SIZE = 3                                 # measured samples are points; only a fit is a line
LABEL_SIZE = 16                                # every axis label and legend, so no panel's text is smaller


def exp_fit(x, a, b, c):
    """
    Exponential fit function for curve fit
    """
    return a * np.exp(-b * x) + c


def get_ch_data(data_file, ch):
    """
    Reads one CAEN channel from a recorded HV CSV.
    Returns (seconds since the first sample as a numpy array, voltages, currents).
    """
    stamps = []
    ch_voltage = []
    ch_current = []

    with open(data_file, "r", newline="") as csvfile:
        for row in csv.reader(csvfile, delimiter=","):
            stamps.append(datetime.fromisoformat(row[0]))
            ch_voltage.append(float(row[1 + (ch * 2)]))
            ch_current.append(float(row[2 + (ch * 2)]))

    seconds = np.array([(stamp - stamps[0]).total_seconds() for stamp in stamps])
    return seconds, ch_voltage, ch_current


def hv_curve_fit(results_path, name, ch, on=True, initial_b=0.02):
    """
    Fits a*exp(-b*t)+c to the ramp: the current after a ramp up (on), the voltage after a
    ramp down (off).

    Returns the raw scipy result (popt, pcov), residuals, time axis.
    """
    time_seconds, ch_voltage, ch_current = get_ch_data(os.path.join(results_path, name), ch)
    data = ch_current if on else ch_voltage

    try:
        p0=[data[0] - data[1], initial_b, data[-1]] # initial guess in general form
        popt, pcov, infodict, mesg, ier = curve_fit(exp_fit, time_seconds, data, p0=p0, full_output=True)
        residuals = infodict["fvec"]
        return(popt, pcov), residuals, time_seconds
    except RuntimeError: # raised if fit fails
        return [[0, 0]], None, None


def _style(ax, is_legend=True):
    """
    Sets style for graphs
    """
    if is_legend:
        legend = ax.legend(loc="best", fontsize=LABEL_SIZE)
        for handle in legend.legend_handles:
            if getattr(handle, "get_marker", lambda: None)() == "o":
                handle.set_markersize(3 * POINT_SIZE)        
    ax.grid(True, color=BAND_HEX, alpha=0.5, linewidth=0.8)
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("0.35")
        spine.set_linewidth(1.2)
    ax.tick_params(labelsize=12)
    ax.ticklabel_format(axis="y", useOffset=False)


def _points(ax, x, y, colour, **kwargs):
    """
    Plot data formatting
    """
    ax.plot(x, y, linestyle="none", marker="o", markersize=POINT_SIZE, color=colour, **kwargs)


def _stacked_figures(title, height_ratios, verdict=None):
    """
    Stacked panels sharing the time axis.
    """
    rows = len(height_ratios)
    fig, axes = plt.subplots(rows, 1, figsize=(12, 6 + rows), sharex=True, gridspec_kw=dict(height_ratios=height_ratios))
    title_text = fig.text(0.06, 0.96, title, fontsize=18, ha="left", va="top")
    if verdict is not None:
        title_box = title_text.get_window_extent(renderer=fig.canvas.get_renderer())
        title_end = fig.transFigure.inverted().transform((title_box.x1, title_box.y0))[0]
        fig.text(title_end + 0.012, 0.96, verdict, fontsize=18, fontweight="bold", ha="left", va="top",
                 color=WIEC_utils.PASS_HEX if verdict == "PASS" else WIEC_utils.FAIL_HEX)
    return fig, axes


def _save(fig, axes, filename, ch, results_path, plot_dir):
    """
    Saves figure to png, returns name of png file
    """
    axes[-1].set_xlabel("Time since recording started (s)", fontsize=LABEL_SIZE)
    fig.tight_layout(rect=[0, 0, 1, 0.92], h_pad=2.0)
    png_name = f"{Path(filename).stem}_ch{ch}.png"
    fig.savefig(os.path.join(plot_dir or results_path, png_name), dpi=90)
    plt.close(fig)
    return png_name


def make_plot(results_path, filename, title, ch, fit=None, on=True, limits=None, other_ylim=None,
              ylim=None, plot_dir=None):
    """
    Plots one channel of a ramp up or down with an exponential fit and time constant.
    """
    seconds, ch_voltage, ch_current = get_ch_data(os.path.join(results_path, filename), ch)

    #Fitted is the channel that is fitted, other is not fitted and for reference
    if on:
        fitted, fitted_label, fitted_hex = ch_current, "Current (µA)", CURRENT_HEX
        other, other_label, other_hex = ch_voltage, "Voltage (V)", VOLTAGE_HEX
    else:
        fitted, fitted_label, fitted_hex = ch_voltage, "Voltage (V)", VOLTAGE_HEX
        other, other_label, other_hex = ch_current, "Current (µA)", CURRENT_HEX
    fitted_name = fitted_label.split(" ")[0].lower()

    # fit None: the phase is not fitted (the terminated ones), so only the data is drawn.
    fit_ok = fit is not None and len(fit) == 2 and len(fit[0]) == 3
    b = float(fit[0][1]) if fit_ok else 0.0
    if fit is None:
        lines = []
    elif not fit_ok:
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

    if on: # current fitted and on top
        fig, (ax, ax_other) = _stacked_figures(title, height_ratios=[3, 1], verdict=verdict)
    else: # voltage fitted and on top
        fig, (ax_other, ax) = _stacked_figures(title, height_ratios=[1, 3], verdict=verdict)

    _points(ax, seconds, fitted, fitted_hex, zorder=3, label=f"Measured {fitted_name}")

    if fit_ok:
        ax.plot(seconds, exp_fit(seconds, *fit[0]), color=FIT_HEX, linewidth=2.5, zorder=4, label="Exponential fit")

    if lines:
        ax.text(0.98, 0.70, "\n".join(lines), transform=ax.transAxes, ha="right", va="top", multialignment="left", fontsize=16,
                bbox=dict(facecolor="white", edgecolor=BAND_HEX, linewidth=2))
    ax.set_ylabel(fitted_label, fontsize=LABEL_SIZE)
    _style(ax)
    if ylim is not None:
        ax.set_ylim(min(ylim[0], min(fitted)), max(ylim[1], max(fitted)))

    _points(ax_other, seconds, other, other_hex)
    ax_other.set_ylabel(other_label, fontsize=LABEL_SIZE)
    _style(ax_other, is_legend=False)
    if other_ylim is not None:
        ax_other.set_ylim(min(other_ylim[0], min(other)), max(other_ylim[1], max(other)))

    # fig.axes is top to bottom, so _save labels the time axis on the bottom panel
    return _save(fig, fig.axes, filename, ch, results_path, plot_dir)


def make_term_plot(results_path, filename, title, ch, graded_v, graded_i, limits, plot_dir=None):
    """
    Plots one channel of a terminated (20 V) ramp up as the resistance that is graded.
    """
    seconds, ch_voltage, ch_current = get_ch_data(os.path.join(results_path, filename), ch)

    # V over uA is MOhm; x1000 for kOhm. A zero current reading is a gap, not a divide by zero
    r_kohm = np.array([1e3 * v / i if i else np.nan for v, i in zip(ch_voltage, ch_current)])
    r_min, r_max = 1e3 * limits[0], 1e3 * limits[1]

    try:
        graded_v, graded_i = float(graded_v), float(graded_i)
        graded_r = 1e3 * graded_v / graded_i
    except (TypeError, ValueError, ZeroDivisionError):
        graded_r = None
    verdict = "PASS" if graded_r is not None and r_min < graded_r < r_max else "FAIL"

    fig, (ax, ax_i, ax_v) = _stacked_figures(title, height_ratios=[3, 1, 1], verdict=verdict)
    ax.axhspan(r_min, r_max, color=WIEC_utils.PASS_HEX, alpha=0.12, label=f"Pass band {r_min:g}–{r_max:g} kΩ")
    _points(ax, seconds, r_kohm, DATA_HEX, label="R = V / I during the hold")

    if graded_r is not None:
        ax.plot(seconds[-1], graded_r, marker="*", markersize=18, color=GRADED_HEX,
                markeredgecolor="black", linestyle="none", zorder=5, label="Reading that is graded")
        ax.annotate(f"R = {graded_v:.2f} V / {graded_i:.1f} µA\n   = {graded_r:.1f} kΩ", (seconds[-1], graded_r), 
                    xytext=(-20, -60), textcoords="offset points", ha="right", fontsize=13, 
                    bbox=dict(facecolor="white", edgecolor=BAND_HEX, linewidth=2), 
                    arrowprops=dict(arrowstyle="->", color="black"))
    else:
        ax.text(0.98, 0.70, "No reading to grade", transform=ax.transAxes, ha="right", va="top",
                fontsize=13, bbox=dict(facecolor="white", edgecolor=BAND_HEX, linewidth=2))

    # Half a band of margin either side; widened if the data (or the star) leaves it
    margin = 0.5 * (r_max - r_min)
    shown = [r for r in r_kohm if np.isfinite(r)] + ([graded_r] if graded_r is not None else [])
    ax.set_ylim(min([r_min - margin] + shown), max([r_max + margin] + shown))
    ax.set_ylabel("Resistance (kΩ)", fontsize=LABEL_SIZE)
    _style(ax)

    for axis, data, label, colour in ((ax_i, ch_current, "Current (µA)", CURRENT_HEX),
                                      (ax_v, ch_voltage, "Voltage (V)", VOLTAGE_HEX)):
        _points(axis, seconds, data, colour)
        axis.set_ylabel(label, fontsize=LABEL_SIZE)
        _style(axis, is_legend=False)
        mid = abs(sum(data) / len(data))
        if mid > 0:
            axis.set_ylim(min(0.75 * mid, min(data)), max(1.25 * mid, max(data)))

    return _save(fig, (ax, ax_i, ax_v), filename, ch, results_path, plot_dir)