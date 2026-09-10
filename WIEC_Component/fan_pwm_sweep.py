"""Fan PWM sweep.

Steps the fan supply from rigol832a_fan_voltage_min to _max and records the
PWM oscillation frequency of every fan at each point, then writes the whole
sweep into the single spreadsheet row as well as a standalone CSV and a set of
PNG curves.
"""

import csv
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from . import cli
from . import fan_test

NAME = "Fan PWM Sweep"
KEY = "fan_pwm_sweep"
DESCRIPTION = "Fan voltage sweep with PWM frequency, read voltage and current curves"
EST_MINUTES = 3


def _save_plot(folder, filename, x, y, title, xlabel, ylabel):
    plt.figure()
    plt.plot(x, y, marker="o")
    plt.title(title, fontsize=14)
    plt.xlabel(xlabel, fontsize=12)
    plt.ylabel(ylabel, fontsize=12)
    plt.grid(True)
    plt.savefig(os.path.join(folder, filename), bbox_inches="tight")
    plt.close()


def run(session):
    json_data = session.json_data
    sheet = session.sheet
    num_fans = int(json_data["keysight970a_fan_num"])

    steps = int(json_data["rigol832a_fan_voltage_number_of_data_samples"])
    fan_voltage_max = json_data["rigol832a_fan_voltage_max"]
    fan_voltage_min = json_data["rigol832a_fan_voltage_min"]

    if steps < 2:
        session.log("CONFIG ERROR! Invalid Number of Data Samples:")
        session.log("Fan Sweep Test's number of data samples must be at least 2")
        session.log("Fix rigol832a_fan_voltage_number_of_data_samples in config.json")
        return False

    if fan_voltage_max <= fan_voltage_min:
        session.log("CONFIG ERROR! Invalid Fan Voltage Range:")
        session.log("Fan Sweep Test's MAXIMUM voltage is <= the MINIMUM voltage")
        session.log("Fix rigol832a_fan_voltage_max / _min in config.json")
        return False

    fan_results_folder = os.path.join(session.results_path, "fan PWM results")
    os.makedirs(fan_results_folder, exist_ok=True)

    # The same points the spreadsheet headers were built from
    prog_voltage = sheet.get_fan_pwm_sweep_points()
    session.log(f"Fan Voltage Sweep Points in Volts : {prog_voltage}")

    fan_signals = {i: [] for i in range(1, num_fans + 1)}
    read_voltage = []
    read_current = []

    nominal_fan_voltage = json_data["rigol832a_fan_voltage"]
    try:
        for sweep_voltage in prog_voltage:
            json_data["rigol832a_fan_voltage"] = sweep_voltage
            session.rigol0.setup_fan()

            readings = fan_test.measure(session)

            read_voltage.append(readings["fan_voltage"])
            read_current.append(readings["fan_current"])
            for fan in range(1, num_fans + 1):
                fan_signals[fan].append(readings["fan_read_signal"].get(fan, -1))
    finally:
        # Put the supply back where the rest of the run expects it
        json_data["rigol832a_fan_voltage"] = nominal_fan_voltage
        try:
            session.rigol0.setup_fan()
        except Exception:
            pass

    session.datastore["fan_pwm_programmed_voltage"] = prog_voltage
    session.datastore["fan_pwm_read_voltage"] = read_voltage
    session.datastore["fan_pwm_read_current"] = read_current
    session.datastore["fan_pwm_signals"] = fan_signals

    for fan in range(1, num_fans + 1):
        _save_plot(
            fan_results_folder,
            f"fan_{fan}_voltage_vs_frequency.png",
            prog_voltage, fan_signals[fan],
            f"Programmed Input Voltage vs. Fan #{fan} PWM Frequency",
            "Programmed Fan Voltage [V]", "Oscillation Frequency [Hz]",
        )
    _save_plot(
        fan_results_folder, "programmed_voltage_vs_read_voltage.png",
        prog_voltage, read_voltage,
        "Programmed Fan Voltage vs. Read Fan Voltage",
        "Programmed Fan Voltage [V]", "Read Fan Voltage [V]",
    )
    _save_plot(
        fan_results_folder, "programmed_voltage_vs_read_current.png",
        prog_voltage, read_current,
        "Programmed Fan Voltage vs. Read Fan Current",
        "Programmed Fan Voltage [V]", "Read Fan Current [A]",
    )

    # One wide row: fan #1 across all sweep points, then fan #2, ...
    csv_headers = sheet.get_fan_pwm_headers(prog_voltage)
    fan_pwm_row = []
    for fan in range(1, num_fans + 1):
        for i in range(len(prog_voltage)):
            fan_pwm_row.append(prog_voltage[i])
            fan_pwm_row.append(read_voltage[i])
            fan_pwm_row.append(read_current[i])
            fan_pwm_row.append(fan_signals[fan][i])

    for col_offset, header in enumerate(csv_headers):
        sheet.ws.cell(
            row=sheet.row, column=sheet.fan_pwm_first_col + col_offset,
            value=fan_pwm_row[col_offset] if col_offset < len(fan_pwm_row) else None,
        )
        sheet.ws.cell(
            row=2, column=sheet.fan_pwm_first_col + col_offset, value=header
        ).style = "top"
    sheet.save()

    csv_save_path = os.path.join(fan_results_folder, "fan_pwm_results.csv")
    with open(csv_save_path, mode="w", newline="") as csv_file:
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow(csv_headers)
        csv_writer.writerow(fan_pwm_row)

    session.log("Fan PWM plots, one-row CSV data, and spreadsheet data saved")
    session.log(f"Fan PWM results folder: {fan_results_folder}")

    # The sweep is characterisation, not a pass/fail gate: it reports data.
    return True


if __name__ == "__main__":
    sys.exit(cli.run_single_item(sys.modules[__name__]))
