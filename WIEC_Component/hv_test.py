""" hv_test.py

For every PCB channel under test, eight phases:

    positive open      0 -> +2000 V and back
    positive 10k       0 -> +20 V and back
    negative open      0 -> -2000 V and back
    negative 10k       0 -> -20 V and back

Each phase logs V/I for all 16 CAEN channels to CSV and plots it. The open phases
also fit a*exp(-b*t)+c to the ramp (the time constant tau = 1/b is graded). The
resistance V/I at the end of each ramp up is checked against the config thresholds:
for the 10k terminated phases that confirms the warm filter board's ~100 kOhm
resistor, and those phases have no time constant.

Relay bytes: the first selects the positive (bit 0) or negative (bit 1) branch,
the second selects open (bit 1) or 10k terminated (bit 0).

-------------------------------------------------------------------------------------------------------
config.json keys used:

"hv_max_connection_retries"                     Maximum number of connection retries
"hv_minutes_duration_short"                     Duration to run 10 kOhm termination phases (minutes)
"hv_minutes_duration_long"                      Duration to run open circuit phases (minutes)
"hv_seconds_interval"                           Time between voltage/current samples (seconds)
"pcb_ch{i}_{polarity}"                          CAEN channel number for PCB channel i, polarity pos/neg
"caenR8033DM_open_voltage"                      Voltage to ramp to for open circuit phases
"caenR8033DM_term_voltage"                      Voltage to ramp to for 10 kOhm termination phases
"hv_stability_wait"                             Seconds to wait for the open circuit phases to stabilize
"hv_termination_wait"                           Seconds to wait for the 10 kOhm termination phases to stabilize
"hv_fit_initial_b"                              Initial b parameter for the exp fit, to help convergence
"hv_plot_voltage_window"                        Plot only: +/- V around the set voltage for the voltage panel under a ramp-up fit
"hv_plot_current_range"                         Plot only: [low, high] uA for the current panel under a ramp-down fit
"hv_resistance_open_min"                        Minimum acceptable open circuit resistance
"hv_resistance_open_max"                        Maximum acceptable open circuit resistance
"hv_resistance_term_min"                        Minimum acceptable 10 kOhm termination resistance
"hv_resistance_term_max"                        Maximum acceptable 10 kOhm termination resistance
"hv_tau_min"                                    Minimum acceptable decay rate b (1/s) of the open-phase exp fit; tau = 1/b
"hv_tau_max"                                    Maximum acceptable decay rate b (1/s) of the open-phase exp fit; tau = 1/b
"simultaneous_test"                             Whether to test all channels at once or one at a time
"channels_to_test"                              List of PCB channels to test
-------------------------------------------------------------------------------------------------------
"""

import csv
import os
import sys
import time
import traceback
import types
from datetime import datetime

from . import analysis
from . import cli
from . import WIEC_utils
from . import record_results

NAME = "HV Test"
KEY = "hv_test"
DESCRIPTION = "HV leakage resistance and RC time constant, 8 phases per channel"
EST_MINUTES = 28

# The original code retried a broken connection forever, which could leave the
# crate energised indefinitely with nobody watching.  Give up after this many.
DEFAULT_MAX_ATTEMPTS = 5


def _max_attempts(session):
    return int(session.json_data.get("hv_max_connection_retries", DEFAULT_MAX_ATTEMPTS))


def record_hv_data(session, name, raw_dir, short_time=False):
    """Logs V and I of all 16 CAEN channels to raw_dir/name (CSV) for the configured duration."""
    json_data = session.json_data
    data = []

    cycle_start_time = time.time()
    prev_measurement = cycle_start_time - 1
    minutes_wait = (
        json_data["hv_minutes_duration_short"]
        if short_time
        else json_data["hv_minutes_duration_long"]
    )

    session.log(
        f"Collecting data for {name} for {minutes_wait} minutes starting at "
        f"{datetime.fromtimestamp(cycle_start_time)}..."
    )

    message = f"Recording {name}"

    try:
        while time.time() - cycle_start_time < (minutes_wait * 60):
            WIEC_utils.draw_bar(message, time.time() - cycle_start_time, minutes_wait * 60)
            if time.time() > prev_measurement + json_data["hv_seconds_interval"]:
                prev_measurement = prev_measurement + json_data["hv_seconds_interval"]
                datum = [datetime.now()]
                for i in range(16):
                    datum.append(session.caen.get_voltage(i))
                    datum.append(session.caen.get_current(i))
                data.append(datum)
            else:
                # Stops this from being a busy-wait between samples
                time.sleep(0.2)
    finally:
        WIEC_utils.finish_bar(message)

    with open(os.path.join(raw_dir, name), "w") as fp:
        csv.writer(fp, delimiter=",").writerows(data)


def _recover_connection(session):
    session.log("Connection broken, attempting to reset...")
    try:
        session.caen.turn_off(list(range(16)), emergency=True)
    except Exception:
        print(traceback.format_exc())
    session.reset_pyvisa_connections()
    session.rigol1.power("ON", "hvpullup")
    session.rigol1.power("ON", "hvpullup2")


def wait_for_ramp_with_bar(self, ch, going_up):
    self.get_voltage(ch)
    self.get_channel_status(ch)
    self.channel_error()
    self.json_data


def wait_for_ramp_with_bar(self, ch, going_up):
    """
    Stands in for CAENR8033DM_WRAPPER.wait_for_ramp on this session's CAEN only (swapped in by
    run(); the shared wrapper file is not changed). Shows the ramp as a progress bar instead of
    a line every second. The bar follows the voltage: how far it has moved, over the ramp rate.
    Same stop and safety checks as the original: done at channel status 1 (on) / 0 (off), and
    channel_error on any status above 0x7, which exits on an overcurrent or trip.
    """
    rate = self.json_data["caenR8033DM_ramp_up" if going_up else "caenR8033DM_ramp_down"]
    start = abs(self.get_voltage(ch))
    target = abs(self.get_HV_value(ch)) if going_up else 0.0
    total_s = abs(target - start) / rate
    done_status = 1 if going_up else 0
    message = f"Channel {ch} ramping up to {target:g} V" if going_up else f"Channel {ch} ramping down"

    drawn = False
    finished = False
    try:
        while True:
            status = self.get_channel_status(ch)
            if status == done_status:
                finished = True
                break
            if status > 0x7:
                self.channel_error(ch, status)
            moved_s = abs(abs(self.get_voltage(ch)) - start) / rate
            WIEC_utils.draw_bar(message, moved_s, total_s)
            drawn = True
            time.sleep(self.ramp_wait)
    finally:
        if drawn and finished:
            WIEC_utils.finish_bar(message)
        elif drawn:
            print("\n\n")   # a trip or Ctrl-C: leave the bar where it stopped, so it doesn't look done


def _ramp_on(session, chs, relay_hv, relay_term, wait_seconds):
    """Sets the relays and ramps the given channels up, with bounded retries."""
    attempts = _max_attempts(session)
    for attempt in range(1, attempts + 1):
        try:
            session.keysight.set_relay(relay_hv, relay_term)
            session.caen.turn_on(chs)
            WIEC_utils.countdown_sleep(session, "HV Stabilization wait time", wait_seconds)
            return
        except (ConnectionResetError, BrokenPipeError):
            print(traceback.format_exc())
            session.log(f"Ramp attempt {attempt}/{attempts} failed")
            _recover_connection(session)

    raise RuntimeError(
        f"Could not ramp channels {chs} after {attempts} attempts -- giving up "
        "so the crate does not sit energised while retrying forever"
    )


def _phase(session, hv_results, chs_to_test, chs_string, spec):
    """Runs one of the eight phases and stores its fit / readings."""
    json_data = session.json_data
    polarity = spec["polarity"]
    key = f"{polarity}_{spec['termination']}"
    chs = [json_data[f"pcb_ch_{i}_{polarity}"] for i in chs_to_test]
    voltage = json_data[spec["voltage_key"]]
    sign = "" if polarity == "pos" else "-"

    if spec["on"]:
        for ch in chs:
            session.caen.set_HV_value(ch, voltage)
            session.log(
                f"Turning Channel {ch} HV from 0 to {sign}{voltage}V "
                f"with {spec['label']}"
            )
        _ramp_on(
            session, chs, spec["relay_hv"], spec["relay_term"],
            json_data[spec["wait_key"]],
        )
    else:
        for ch in chs:
            session.log(
                f"Turning Channel {ch} HV from {sign}{voltage}V to 0 "
                f"with {spec['label']}"
            )
        session.caen.turn_off(chs)

    suffix = "on" if spec["on"] else "off"
    csv_name = f"{session.test_name}_ch{chs_string}_{key}_{suffix}.csv"
    # Raw data and plots go into this run's folder: raw/hv_test/ and plots/hv_test/
    raw_dir = record_results.test_folder(session, "raw", KEY)
    plot_dir = record_results.test_folder(session, "plots", KEY)
    record_hv_data(session, csv_name, raw_dir, short_time=spec["short_time"])

    for i in chs_to_test:
        ch = json_data[f"pcb_ch_{i}_{polarity}"]
        # Only the open phases are fitted. The terminated (20 V) phases check the termination
        # resistor through V/I, and there is no time constant to measure in them
        fit = None
        if spec["termination"] == "open":
            fit = analysis.hv_curve_fit(
                raw_dir, csv_name, ch,
                on=spec["on"],
                initial_b=float(json_data["hv_fit_initial_b"])
            )
            hv_results[i][f"{key}_{suffix}_fit"] = fit

        if spec["on"]:
            # Read twice; the first reading back from the CAEN can be stale
            session.caen.get_voltage(ch)
            hv_results[i][f"{key}_V"] = session.caen.get_voltage(ch)
            session.caen.get_current(ch)
            hv_results[i][f"{key}_I"] = session.caen.get_current(ch)

        direction = f"0 to {sign}{voltage}V" if spec["on"] else f"{sign}{voltage} to 0V"
        title = f"Ch {i}{'+' if polarity == 'pos' else '-'} from {direction}, {spec['label']}"
        hv_results[i][f"{key}_{suffix}_plot_title"] = title
        # Fixed bounds for the panel under the fit: the voltage around its set value after a
        # ramp up, the current on a fixed scale after a ramp down
        if spec["on"]:
            window = json_data["hv_plot_voltage_window"]
            other_ylim = (voltage - window, voltage + window)
        else:
            other_ylim = tuple(json_data["hv_plot_current_range"])
        hv_results[i][f"{key}_{suffix}_plot"] = analysis.make_plot(
            raw_dir, csv_name, title, ch,
            fit=fit, on=spec["on"],
            # no limits for an unfitted phase: no time constant box, no PASS/FAIL in the title
            limits=(json_data["hv_tau_min"], json_data["hv_tau_max"]) if fit is not None else None,
            other_ylim=other_ylim,
            plot_dir=plot_dir,
        )


def _phase_specs(relay_setting):
    """The eight phases, in the order the crate is exercised."""
    return [
        # positive, open termination
        dict(polarity="pos", termination="open", on=True, label="open termination",
             voltage_key="caenR8033DM_open_voltage", wait_key="hv_stability_wait",
             relay_hv=0, relay_term=relay_setting, short_time=False),
        dict(polarity="pos", termination="open", on=False, label="open termination",
             voltage_key="caenR8033DM_open_voltage", wait_key="hv_stability_wait",
             relay_hv=0, relay_term=relay_setting, short_time=False),
        # positive, 10k termination
        dict(polarity="pos", termination="term", on=True, label="termination resistor",
             voltage_key="caenR8033DM_term_voltage", wait_key="hv_termination_wait",
             relay_hv=0, relay_term=0, short_time=True),
        dict(polarity="pos", termination="term", on=False, label="termination resistor",
             voltage_key="caenR8033DM_term_voltage", wait_key="hv_termination_wait",
             relay_hv=0, relay_term=0, short_time=True),
        # negative, open termination
        dict(polarity="neg", termination="open", on=True, label="open termination",
             voltage_key="caenR8033DM_open_voltage", wait_key="hv_stability_wait",
             relay_hv=relay_setting, relay_term=relay_setting, short_time=False),
        dict(polarity="neg", termination="open", on=False, label="open termination",
             voltage_key="caenR8033DM_open_voltage", wait_key="hv_stability_wait",
             relay_hv=relay_setting, relay_term=relay_setting, short_time=False),
        # negative, 10k termination
        dict(polarity="neg", termination="term", on=True, label="termination resistor",
             voltage_key="caenR8033DM_term_voltage", wait_key="hv_termination_wait",
             relay_hv=relay_setting, relay_term=0, short_time=True),
        dict(polarity="neg", termination="term", on=False, label="termination resistor",
             voltage_key="caenR8033DM_term_voltage", wait_key="hv_termination_wait",
             relay_hv=relay_setting, relay_term=0, short_time=True),
    ]


def test_channels(session, chs_to_test, hv_results):
    for i in chs_to_test:
        hv_results.setdefault(i, {})

    chs_string = "".join(f"{i}_" for i in chs_to_test)
    relay_setting = 0
    for i in chs_to_test:
        relay_setting |= 1 << i

    for spec in _phase_specs(relay_setting):
        _phase(session, hv_results, chs_to_test, chs_string, spec)
        if spec["termination"] == "term" and spec["on"]:
            time.sleep(5)


def _evaluate(session, chs_to_test, hv_results):
    """Turns the raw readings into resistances and pass/fail cells."""
    json_data = session.json_data
    sheet = session.sheet
    result = True

    for i in chs_to_test:
        # Voltage in volts over current in microamps gives megaohms
        for key in ["pos_open", "pos_term", "neg_open", "neg_term"]:
            try:
                hv_results[i][f"{key}_R"] = float(hv_results[i][f"{key}_V"]) / float(
                    hv_results[i][f"{key}_I"]
                )
            except Exception as exc:
                hv_results[i][f"{key}_R"] = 0
                session.log(f"Could not compute {key}_R for channel {i}: {exc}")

        session.log(f"Channel {i} HV results are {hv_results[i]}")

        # The CAEN readings each resistance comes from: recorded, not graded
        for key in ["pos_open", "pos_term", "neg_open", "neg_term"]:
            termination = key.split("_")[1]
            programmed = json_data[f"caenR8033DM_{termination}_voltage"]
            record_results.measure(session, KEY, f"{key} voltage", hv_results[i].get(f"{key}_V"),
                                   unit="V", channel=i, setpoint=programmed)
            record_results.measure(session, KEY, f"{key} current", hv_results[i].get(f"{key}_I"),
                                   unit="uA", channel=i)

        for num, j in enumerate(["pos_open_R", "neg_open_R"]):
            value = float(hv_results[i][j])
            passed = (
                json_data["hv_resistance_open_min"]
                < value
                < json_data["hv_resistance_open_max"]
            )
            sheet.write(
                sheet.hv_res_first_col + (i * sheet.hv_cols) + (num * 6),
                f"{round(value, session.rounding_factor)}Mohm", passed,
            )
            session.datastore["Tests"][f"hv_test_ch{i}_{j}"] = "Pass" if passed else "Fail"
            # The resistance comes from the readings at the end of the ramp up, the "on" plot
            record_results.measure(session, KEY, f"{j[:-2]} resistance", value, passed=passed,
                                   unit="Mohm", channel=i,
                                   low=json_data["hv_resistance_open_min"],
                                   high=json_data["hv_resistance_open_max"],
                                   plot=hv_results[i].get(f"{j[:-2]}_on_plot"),
                                   plot_title=hv_results[i].get(f"{j[:-2]}_on_plot_title"))
            result = result and passed

        for num, j in enumerate(["pos_term_R", "neg_term_R"]):
            value = float(hv_results[i][j])
            passed = (
                json_data["hv_resistance_term_min"]
                < value
                < json_data["hv_resistance_term_max"]
            )
            sheet.write(
                sheet.hv_res_first_col + 3 + (i * sheet.hv_cols) + (num * 6),
                f"{round(value * 1e3, session.rounding_factor)}kohm", passed,
            )
            session.datastore["Tests"][f"hv_test_ch{i}_{j}"] = "Pass" if passed else "Fail"
            record_results.measure(session, KEY, f"{j[:-2]} resistance", value, passed=passed,
                                   unit="Mohm", channel=i,
                                   low=json_data["hv_resistance_term_min"],
                                   high=json_data["hv_resistance_term_max"],
                                   plot=hv_results[i].get(f"{j[:-2]}_on_plot"),
                                   plot_title=hv_results[i].get(f"{j[:-2]}_on_plot_title"))
            result = result and passed

        # The fit reports b; the spreadsheet carries the time constant 1/b.
        # Open phases only: the terminated phases are not fitted, so their spreadsheet
        # columns stay empty (num keeps the column positions of the full list)
        for num, j in enumerate(["pos_open", "pos_term", "neg_open", "neg_term"]):
            if j.endswith("_term"):
                continue
            for offset, suffix in ((1, "on"), (2, "off")):
                fit_key = f"{j}_{suffix}_fit"
                b = float(hv_results[i][fit_key][0][1])
                tau = 1.0 / b if b != 0.0 else 0.0
                passed = json_data["hv_tau_min"] < b < json_data["hv_tau_max"]
                sheet.write(
                    sheet.hv_res_first_col + offset + (i * sheet.hv_cols) + (num * 3),
                    round(tau, session.rounding_factor), passed,
                )
                session.datastore["Tests"][
                    f"hv_{suffix}_fit_test_ch{i}_{fit_key}"
                ] = "Pass" if passed else "Fail"
                # b is what is graded (against hv_tau_min/max); the spreadsheet shows tau = 1/b
                record_results.measure(session, KEY, f"{j} {suffix} fit b", b, passed=passed,
                                       unit="1/s", channel=i,
                                       low=json_data["hv_tau_min"], high=json_data["hv_tau_max"],
                                       note=f"tau = {round(tau, session.rounding_factor)} s",
                                       plot=hv_results[i].get(f"{j}_{suffix}_plot"),
                                       plot_title=hv_results[i].get(f"{j}_{suffix}_plot_title"))
                result = result and passed

        session.datastore[f"hv_ch{i}"] = dict(hv_results[i])

    sheet.save()
    return result


def run(session):
    json_data = session.json_data
    hv_results = {}

    # Ramps in this test show a progress bar (see wait_for_ramp_with_bar)
    caen = session.caen
    caen.wait_for_ramp = types.MethodType(wait_for_ramp_with_bar, caen)

    session.rigol1.power("ON", "hvpullup")
    session.rigol1.power("ON", "hvpullup2")

    try:
        if json_data["simultaneous_test"] == "True":
            groups = [list(json_data["channels_to_test"])]
        else:
            groups = [[ch] for ch in json_data["channels_to_test"]]

        for group in groups:
            try:
                test_channels(session, group, hv_results)
            except (ConnectionResetError, BrokenPipeError):
                print(traceback.format_exc())
                _recover_connection(session)
                session.log(f"Retrying channel group {group} once")
                test_channels(session, group, hv_results)
    finally:
        for _ in range(_max_attempts(session)):
            try:
                session.rigol1.power("OFF", "hvpullup")
                session.rigol1.power("OFF", "hvpullup2")
                break
            except Exception:
                print(traceback.format_exc())
                session.reset_pyvisa_connections()

    tested = [ch for group in groups for ch in group]
    return _evaluate(session, tested, hv_results)


if __name__ == "__main__":
    sys.exit(cli.run_single_item(sys.modules[__name__]))
