"""Curve fitting and plotting shared by the HV items."""


import csv
import os
from datetime import datetime
from pathlib import Path

import matplotlib

# These items only ever write PNGs, and they routinely run over ssh, so keep
# matplotlib off any display.
matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import curve_fit


def exp_fit(x, a, b, c):
    return a * np.exp(-b * x) + c


def get_ch_data(data_file, ch):
    """Reads the time / voltage / current columns for one CAEN channel."""
    ch_datetime = []
    ch_voltage = []
    ch_current = []

    with open(data_file, "r", newline="") as csvfile:
        for row in csv.reader(csvfile, delimiter=","):
            ch_datetime.append(datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S.%f"))
            ch_voltage.append(float(row[1 + (ch * 2)]))
            ch_current.append(float(row[2 + (ch * 2)]))

    first_time = ch_datetime[0]
    ch_timedelta = [i - first_time for i in ch_datetime]
    ch_time = [
        datetime(2024, 1, 1, 0, i.seconds // 60 % 60, i.seconds % 60, 0)
        for i in ch_timedelta
    ]
    return ch_time, ch_voltage, ch_current


def hv_curve_fit(results_path, name, ch, on=True, term=False, initial_b=0.02):
    """Fits a*exp(-b*t)+c to the ramp.

    Returns the raw scipy result; fit[0][1] is b, and the spreadsheet reports
    the time constant as 1/b.  A fit that does not converge comes back as
    [[0, 0]], which fails the threshold check rather than crashing the run.

    Added initial parameters to allow for better convergence.
    initial_b is included in config.json for adjustment while calling the function.
    """
    ch_time, ch_voltage, ch_current = get_ch_data(
        os.path.join(results_path, name), ch
    )

    if term:
        ch_current = [i / 1000 for i in ch_current]

    data = ch_current if on else ch_voltage

    first_timestamp = ch_time[0].timestamp()
    time_seconds = [dt.timestamp() - first_timestamp for dt in ch_time]

    try:
        p0=[data[0] - data[1],
            initial_b,
            data[-1]]
        return curve_fit(exp_fit, time_seconds, data, p0=p0)
    except RuntimeError:
        return [[0, 0]]


def format_plot(ax):
    tick_size = 18
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%M:%S"))
    ax.tick_params(axis="x", labelsize=tick_size, colors="black")
    ax.tick_params(axis="y", labelsize=tick_size, colors="black")


def make_plot(results_path, filename, name, ch, fit=None, axes=None, plot_dir=None):
    """Plots one channel of a recorded HV CSV. The CSV is read from results_path; the PNG is
    saved in plot_dir if given, otherwise next to the CSV in results_path. Returns the PNG's file name."""
    ch_time, ch_voltage, ch_current = get_ch_data(
        os.path.join(results_path, filename), ch
    )

    fig = plt.figure(figsize=(16, 12), dpi=80)
    ax = fig.add_subplot(1, 1, 1)
    ax.plot(ch_time, ch_current, label="Ch Current")
    format_plot(ax)

    ax2 = ax.twinx()
    ax2.plot(ch_time, ch_voltage, label="Ch Voltage", color="red")

    fig.suptitle(name, fontsize=36)
    ax.set_xlabel("Time (Minutes:Seconds)", fontsize=24)
    ax.set_ylabel("Current (uA)", fontsize=24)
    if axes:
        ax2.set_ylim([axes[0], axes[1]])
    ax2.set_ylabel("Voltage (V)", fontsize=24)
    format_plot(ax2)

    if fit:
        textstr = "k = %.4f s" % fit
        props = dict(boxstyle="round", facecolor="wheat", alpha=0.5)
        ax.text(
            0.75, 0.75, textstr, transform=ax.transAxes, fontsize=24,
            verticalalignment="top", bbox=props,
        )

    fig.legend(loc="lower left", prop={"size": 20}, ncol=2)
    png_name = f"{Path(filename).stem}_ch{ch}.png"
    fig.savefig(os.path.join(plot_dir or results_path, png_name))
    plt.close(fig)
    return png_name
