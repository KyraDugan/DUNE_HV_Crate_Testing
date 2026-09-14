"""DUNE WIEC HV module test -- one iseg HV module in an MPOD slot.

An operator-guided flow, built out of the three step kinds in operator.py:

    1. Visual inspection            USER / CHECK
    2. Insert module, SN, LED       USER / AUTO / CHECK
    3. Safety jumper loop test      three configurations, generated
    4. HV ramp test                 AUTO, 0 -> 1000 V @ 10 V/s, hold, back down
    5. HV limitation test           AUTO, parameters still TBD

Unlike the other WIEC_Component items this one tests a *module*, not the
crate, so it lives in EXTRA_ITEMS.  Everything it writes goes into
"HV Module Test/", never into the crate workbook.

Run it from the top script, or on its own:

    python3 -m WIEC_Component.hv_module_test config.json my_test_name
    python3 -m WIEC_Component.hv_module_test config.json --dry-run

--dry-run answers its own prompts and talks to a simulated crate, so the flow
text and the pass/fail logic can be checked without any hardware.
"""

import csv
import json
import math
import os
import sys
import time
import traceback
from datetime import datetime

from . import hv_limit_adjust
from . import popups
from .hv_limit_adjust import IMAX_STAGE, VMAX_STAGE
from .iseg_mpod import ModuleInfo, SNMPError, TripError
from .operator import (
    AUTO, CHECK, USER, UNSAFE,
    AbortFlow, AutoConsole, Console, FlowRunner, Step,
)

NAME = "HV Module Test"
KEY = "hv_module_test"
DESCRIPTION = "Operator-guided QC of one iseg HV module in an MPOD slot"
EST_MINUTES = 15

RESULTS_ROOT = "HV Module Test"

DEFAULTS = {
    "iseg_ip": "169.254.4.31",
    "iseg_slot": 2,
    "iseg_read_community": "public",
    "iseg_write_community": "guru",

    # "snmp": the software power cycles the crate itself with sysMainSwitch.
    # Measured on this crate: the module bay really does go dead (0 modules,
    # 0 outputs, fans stop) and comes back in ~13 s, so no one has to touch the
    # rear panel.  "manual_mains" keeps the operator in the loop for a site
    # that requires a true AC disconnect before a module is handled.
    "iseg_power_cycle_mode": "snmp",
    "iseg_power_off_timeout_s": 120.0,
    "iseg_boot_timeout_s": 240.0,
    # The SNMP table clears the instant the bay is switched off, but the fans
    # take a few seconds to stop and the rails a few more to discharge.  Wait
    # this long after the bay reports dead before anyone pulls a module.
    "iseg_power_off_settle_s": 10.0,
    # The bay enumerates progressively -- slot 2 shows up about 13 s after
    # power on, behind other slots.  Give the module a little longer after its
    # description appears before reading or writing any of its settings.
    "iseg_power_on_settle_s": 5.0,

    # The operator pop-ups: the pages of GUI/ISEG_Qualification_Protocol.pdf,
    # exported to GUI/protocol_pngs/<page>.png.  Set false for a run over ssh,
    # or anywhere a full-screen window would be in the way -- the terminal
    # prompts carry the whole test on their own, as they always did.
    "iseg_popups_enabled": True,
    "iseg_popup_dir": "GUI/protocol_pngs",

    "iseg_ramp_target_voltage": 1000.0,
    "iseg_ramp_rate": 10.0,
    "iseg_ramp_hold_seconds": 30.0,
    "iseg_ramp_sample_interval_s": 1.0,
    "iseg_ramp_timeout_factor": 1.5,
    "iseg_ramp_timeout_margin_s": 30.0,
    "iseg_ramp_reach_tolerance_v": 10.0,
    "iseg_current_limit_a": 0.001,

    # Independent voltage check during the ramp.  The HV outputs go through a
    # divider into 34970A channels 111-118 (u200->111 ... u207->118), the same
    # wiring iseg_HV_electrical_test.py uses.  Off by default: it needs that
    # wiring, and the ramp works fine without it.
    "iseg_ramp_daq_enabled": False,
    "iseg_ramp_daq_first_channel": 111,
    # One DAQ scan of eight channels takes 1.7 s, against 0.09 s for an SNMP
    # sample, so the DAQ gets its own slower interval.
    "iseg_ramp_daq_interval_s": 5.0,
    # The divider is 10M : 500k, so nominally 21.0 -- but the July data implies
    # 43.5-45.0, a factor of about 2.1 out, and that is not explained yet.
    # Until it is, the DAQ readings are only RECORDED: nothing is derived from
    # this ratio and nothing is graded on it.  It goes into the results purely
    # as the reference number to compare the observation against by eye.
    "iseg_ramp_divider_top_ohm": 10000000.0,
    "iseg_ramp_divider_bottom_ohm": 500000.0,

    # The safety loop bit is assumed to follow the green LED (both the jumper
    # and the test kit have to be present to close it).  Set false to record
    # the reading without grading it.
    "iseg_grade_safety_loop": True,
    # The ramp rate is always measured and recorded; grading it is off by
    # default so a slightly slow module does not fail the whole QC.
    "iseg_grade_ramp_rate": False,
    "iseg_ramp_rate_tolerance_fraction": 0.20,
    # The ramp rate readback is derived from the module percentage, so it can
    # be quantised a little; this is the "did the write take at all?" guard.
    "iseg_ramp_rate_readback_tolerance_fraction": 0.05,

    # Section 5 -- the potentiometer adjustment is defined and graded; the
    # "output reaches 1.2 kV but not 1.3 kV" part of the procedure is not
    # written yet, so it is not attempted.
    "iseg_limit_test_enabled": True,
    # The 34970A on GPIB watches the potentiometer control voltage.  This is
    # NOT the LAN DAQ970A in keysight970a -- different instrument.
    #
    # Vmax and Imax are separate potentiometer outputs and need a channel
    # each.  Confirmed by the tester 2026-08-21: slot-1 channel 19 (= 119) is
    # Imax and 20 (= 120) is Vmax -- note that order, it is the opposite of
    # the way round the labels usually get written.  111-118 are the eight HV
    # channel outputs (iseg_HV_electrical_test.gpib_channel), which is why
    # only these two were left.
    "iseg_limit_vmax_daq_channel": 20,
    "iseg_limit_imax_daq_channel": 19,
    "iseg_limit_gpib_board": 0,
    "iseg_limit_gpib_address": 9,
    # 1.5625 V on the voltage pot = 62.5 % = 1.25 kV on a 2 kV module.
    # 0.15625 V on the current pot = 6.25 % = 0.25 mA on a 4 mA module
    # (confirmed: the "0.15625 V -> 4 mA" in the original proposal was a typo).
    "iseg_limit_expected_voltage_percent": 62.5,
    "iseg_limit_expected_current_percent": 6.25,
    "iseg_limit_percent_tolerance": 2.0,
    "iseg_limit_reach_voltage": 1200.0,
    "iseg_limit_block_voltage": 1300.0,
}


def _cfg(json_data):
    """Config with the item's defaults filled in for anything missing."""
    merged = dict(DEFAULTS)
    for key, value in json_data.items():
        if key in DEFAULTS:
            merged[key] = value
    return merged


# ----------------------------------------------------------------------
# Context passed to every step action
# ----------------------------------------------------------------------
class Context:
    def __init__(self, session, mpod, console, cfg, dry_run=False):
        self.session = session
        self.mpod = mpod
        self.console = console
        self.cfg = cfg
        self.dry_run = dry_run

        self.slot = int(cfg["iseg_slot"])
        self.module_id = f"ma{self.slot}"
        self.channels = [f"u{self.slot * 100 + i}" for i in range(8)]
        self.module = None          # ModuleInfo, once the crate has been read
        self.polarity = -1
        self.nominal_voltage = 0.0

        self.answers = {}           # operator-typed values, by input_key
        self.data = {}              # everything that ends up in the JSON
        self.ramp_samples = []      # rows written to the ramp CSV
        self.daq_samples = []       # independent 34970A readings during the ramp
        self.results_dir = None
        self.runner = None
        self._daq = None
        self.daq_channel = None

    def log(self, message):
        if self.session is not None:
            self.session.log(message)
        else:
            print(f"HV Module Test --> {message}")

    def daq(self):
        """The 34970A, opened on first use and closed once at the end."""
        if self._daq is None and self.dry_run:
            self._daq = SimulatedDAQ()
        if self._daq is None:
            try:
                import gpib  # noqa: F401
            except ImportError:
                raise RuntimeError(
                    "linux-gpib is not available to this interpreter "
                    f"({sys.executable}). The limit adjustment reads the "
                    "34970A over GPIB; run the test with /usr/bin/python3 "
                    "rather than the project venv."
                )
            self._daq = hv_limit_adjust.DAQ34970A(
                board=int(self.cfg["iseg_limit_gpib_board"]),
                address=int(self.cfg["iseg_limit_gpib_address"]),
            )
            self.log(f"34970A: {self._daq.idn()}")
        return self._daq

    def close_daq(self):
        if self._daq is not None:
            self._daq.close()
            self._daq = None

    def serial(self):
        if self.module and self.module.serial_number:
            return self.module.serial_number
        return self.answers.get("expected_serial", "unknown")

    def make_results_dir(self):
        """Where this run writes.

        A dry run goes to "dry-run/", never to "results/".  Real measurements
        and simulated ones sharing a directory is how a genuine run's data got
        deleted while tidying up after a --dry-run: keeping them apart means
        that clean-up cannot reach anything that was measured.
        """
        if self.results_dir:
            return self.results_dir
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        parent = "dry-run" if self.dry_run else "results"
        self.results_dir = os.path.join(
            RESULTS_ROOT, parent, f"{stamp}_SN{self.serial()}"
        )
        os.makedirs(self.results_dir, exist_ok=True)
        return self.results_dir


# ----------------------------------------------------------------------
# Bring the module to a safe state -- called on abort, Ctrl-C and at the end
# ----------------------------------------------------------------------
def safe_state(ctx, power_off_crate=True):
    """HV to 0, channels off, crate off.  Every step guarded on its own."""
    problems = []

    try:
        for channel in ctx.channels:
            try:
                ctx.mpod.set_voltage(channel, 0.0)
            except SNMPError as exc:
                problems.append(f"{channel} set 0 V: {exc}")
        ctx.mpod.wait_until_zero(ctx.channels, timeout_s=180.0)
    except Exception as exc:
        problems.append(f"ramp down: {exc}")

    try:
        problems.extend(ctx.mpod.all_off(ctx.channels))
    except Exception as exc:
        problems.append(f"channels off: {exc}")

    if power_off_crate:
        try:
            ctx.mpod.turn_off_crate()
        except Exception as exc:
            problems.append(f"sysMainSwitch off: {exc}")

    if problems:
        ctx.log("!! safe-state problems: " + "; ".join(problems))
        ctx.log("!! CHECK THE HARDWARE BY HAND before leaving")
    else:
        ctx.log("Module is in a safe state (0 V, channels off, crate off)")
    return not problems


# ----------------------------------------------------------------------
# AUTO actions
# ----------------------------------------------------------------------
def outputs_off(ctx):
    """Switch the outputs and the crate off before the mains goes.

    Skipped when the bay has no power: the channels do not exist then, and
    trying to switch off eight OIDs that answer noAccess only wastes time.
    """
    if not ctx.mpod.is_alive():
        return True, "crate already unreachable"

    powered, reason = ctx.mpod.module_bay_powered()
    if not powered:
        return True, f"nothing to switch off -- {reason}"

    failures = ctx.mpod.all_off(ctx.channels)
    ctx.mpod.turn_off_crate()
    if failures:
        return True, f"sysMainSwitch off; {len(failures)} channel(s) would not switch off"
    return True, "outputs off, sysMainSwitch off"


def _poll_with_progress(ctx, check, timeout_s, message, poll_s=3.0):
    """Polls until check(ctx) returns (True, state), printing a live line.

    The line matters: these waits can run for a minute or more, and a single
    static message reads as a hang.
    """
    started_at = time.monotonic()
    while True:
        done, state = check(ctx)
        elapsed = time.monotonic() - started_at
        if done:
            print("\r" + " " * 100 + "\r", end="")
            return True, elapsed, state

        print(
            f"\r    {message}: {elapsed:5.1f} s / {timeout_s:.0f} s  -- {state} ",
            end="", flush=True,
        )
        if elapsed >= timeout_s:
            print()
            return False, elapsed, state
        time.sleep(poll_s)


def _settle(ctx, seconds, message):
    """A visible wait, so a deliberate pause is never mistaken for a hang."""
    end = time.monotonic() + seconds
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            break
        print(f"\r    {message}: {remaining:4.1f} s ", end="", flush=True)
        time.sleep(min(0.5, remaining))
    print("\r" + " " * 80 + "\r", end="")


def _bay_is_dead(ctx):
    """Is it safe to pull a module out?

    Two acceptable states.  The crate can disappear from the network
    altogether, or -- and this is what this crate actually does -- the
    controller can keep running on standby while the module bay itself is
    unpowered.  Waiting only for SNMP silence would wait for ever here, so the
    module bay is checked directly.
    """
    if not ctx.mpod.is_alive():
        return True, "crate is not answering at all"

    powered, reason = ctx.mpod.module_bay_powered()
    return (not powered), reason


def confirm_crate_dead(ctx):
    """The operator may have typed y without touching the mains switch.

    This is the one step that cannot be answered by typing: the previous step
    already asked the operator to confirm, and the whole point here is to check
    that claim electrically before a module is pulled out of a live crate.

    A timeout is not treated as a hard failure straight away -- forgetting the
    switch is an ordinary mistake, and throwing away the run for it would just
    tempt people to skip the check.  The operator gets told what is wrong and
    can flip the switch and let it look again.
    """
    timeout = float(ctx.cfg["iseg_power_off_timeout_s"])

    while True:
        dead, elapsed, state = _poll_with_progress(
            ctx, _bay_is_dead, timeout_s=timeout,
            message="Waiting for the module bay to lose power",
        )
        if dead:
            settle = float(ctx.cfg["iseg_power_off_settle_s"])
            if settle > 0:
                _settle(ctx, settle, "Letting the fans stop and the rails discharge")
            return True, f"{state} (after {elapsed:.0f} s)"

        if ctx.cfg["iseg_power_cycle_mode"] == "snmp":
            body = (
                f"After {timeout:.0f} s the crate at {ctx.cfg['iseg_ip']} still "
                f"reports: {state}.\n\n"
                "The software asked it to switch off over SNMP and it has not "
                "done so.  DO NOT insert or remove a module.\n\n"
                "Switch the crate off by hand, or stop the test."
            )
        else:
            body = (
                f"After {timeout:.0f} s the crate at {ctx.cfg['iseg_ip']} still "
                f"reports: {state}.\n\n"
                "DO NOT insert or remove a module: iseg modules are not "
                "hot-swappable.\n\n"
                "Switch the AC mains off now. Nothing needs to be typed -- the "
                "test carries on by itself once the module bay goes dead."
            )
        ctx.console.warn("MODULE BAY IS STILL POWERED", body)
        if not ctx.console.ask_yes_no("Keep waiting for the module bay to go off?"):
            return False, (
                f"the module bay at {ctx.cfg['iseg_ip']} was still powered "
                f"after {timeout:.0f} s -- {state}"
            )


def wait_for_crate(ctx):
    """Bring the bay back and wait until the slot under test is enumerated.

    Works for both power cycle modes: in "snmp" the controller never went
    away and just needs sysMainSwitch set, in "manual_mains" it has to finish
    booting first.
    """
    timeout = float(ctx.cfg["iseg_boot_timeout_s"])

    if ctx.mpod.is_alive() and not ctx.mpod.main_switch():
        ctx.console.info("Switching the crate on (sysMainSwitch)...")
        ctx.mpod.turn_on_crate()

    def _module_is_back(c):
        """The bay enumerates progressively, so wait for OUR slot.

        Watching only moduleNumber > 0 let the flow carry on while slot 2 was
        still missing from the table.
        """
        if not c.mpod.is_alive():
            return False, "crate is not answering yet"
        count = c.mpod.module_count()
        if count <= 0:
            return False, "controller is up, module bay not powered yet"
        if not c.mpod.module_present(c.slot):
            return False, f"{count} modules enumerated, slot {c.slot} not there yet"
        return True, f"{count} modules enumerated, slot {c.slot} present"

    while True:
        alive, elapsed, state = _poll_with_progress(
            ctx, _module_is_back, timeout_s=timeout,
            message="Waiting for the crate to come back",
        )
        if alive:
            break

        ctx.console.warn(
            "CRATE HAS NOT COME BACK",
            f"After {timeout:.0f} s the crate at {ctx.cfg['iseg_ip']} reports: "
            f"{state}.\n\n"
            "Check that the AC mains switch is ON and that the network cable "
            "is connected.",
        )
        if not ctx.console.ask_yes_no("Keep waiting for the crate to come up?"):
            return False, f"the crate did not come back within {timeout:.0f} s -- {state}"

    settle = float(ctx.cfg["iseg_power_on_settle_s"])
    if settle > 0:
        _settle(ctx, settle, f"Letting slot {ctx.slot} finish coming up")

    ctx.console.info(f"Crate came back after {elapsed:.0f} s: {state}")
    return True, (
        f"crate is up after {elapsed:.0f} s ({state}), sysMainSwitch on, "
        f"settled {settle:g} s"
    )


def read_serial(ctx):
    """Reads the module and compares it with what the operator typed."""
    module = ctx.mpod.module_in_slot(ctx.slot)
    ctx.module = module
    ctx.data["module"] = {
        "slot": module.slot, "model": module.model,
        "serial_number": module.serial_number, "firmware": module.firmware,
        "description": module.description,
    }

    if not module.is_iseg:
        return False, f"slot {ctx.slot} holds a {module.vendor} {module.model}, not an iseg module"

    ctx.nominal_voltage = ctx.mpod.nominal_voltage(module.channels[0])
    ctx.polarity = -1 if ctx.nominal_voltage < 0 else 1
    ctx.channels = module.channels
    ctx.data["polarity"] = "negative" if ctx.polarity < 0 else "positive"
    ctx.data["nominal_voltage_v"] = ctx.nominal_voltage

    expected = ctx.answers.get("expected_serial", "").strip()
    detail = (
        f"{module.model} SN {module.serial_number} fw {module.firmware}, "
        f"{ctx.data['polarity']} polarity, {abs(ctx.nominal_voltage):g} V full scale"
    )

    if expected and expected != module.serial_number:
        ctx.console.warn(
            "SERIAL NUMBER MISMATCH",
            f"Entered by operator : {expected}\n"
            f"Read from module    : {module.serial_number}\n\n"
            "Either the wrong module is in the slot, or the number was typed "
            "incorrectly.",
        )
        return False, f"expected SN {expected}, module reports {module.serial_number}"

    return True, detail


def check_module_health(ctx):
    problems = ctx.mpod.module_health(ctx.module_id)
    if problems:
        return False, "; ".join(problems)
    return True, "module, supply and temperature all report good"


def check_module_good(ctx, expect_good):
    """moduleStatus bit 12 -- this tracks the green LED exactly.

    Set only when the jumper AND the test kit are both fitted, which is the
    same condition as the LED.  So the operator's visual answer does get an
    independent cross-check.
    """
    good, detail = ctx.mpod.module_good(ctx.module_id)
    wanted = "good" if expect_good else "not good"
    passed = good == expect_good
    detail = f"module reads {'good' if good else 'not good'} ({detail}), expected {wanted}"
    if not ctx.cfg["iseg_grade_safety_loop"]:
        return True, detail + " (not graded)"
    return passed, detail


def check_safety_loop(ctx, expect_closed):
    """moduleStatus bit 10 -- 'Safety loop is closed'.

    Follows the SAFETY JUMPER, and only the jumper: closed with it fitted
    whether or not the test kit is, open with it removed.  Removing it also
    latches moduleIsEventActive(11).
    """
    closed = ctx.mpod.safety_loop_closed(ctx.module_id)
    state = "closed" if closed else "open"
    wanted = "closed" if expect_closed else "open"
    passed = closed == expect_closed
    detail = f"safety loop reads {state}, expected {wanted}"
    if not ctx.cfg["iseg_grade_safety_loop"]:
        # Recorded but not graded
        return True, detail + " (not graded)"
    return passed, detail


# ----------------------------------------------------------------------
# Section 4 -- the HV ramp
# ----------------------------------------------------------------------
def _sample(ctx, phase, started_at, target_v):
    """One reading of all channels, appended to the CSV rows.  Raises on a trip."""
    now = time.monotonic()
    reading = ctx.mpod.read_all(ctx.channels)
    timestamp = datetime.now().isoformat(timespec="milliseconds")

    tripped = []
    for channel, values in reading.items():
        ctx.ramp_samples.append({
            "phase": phase,
            "timestamp": timestamp,
            "elapsed_s": round(now - started_at, 3),
            "channel": channel,
            "target_voltage_v": target_v,
            "voltage_v": values["voltage"],
            "current_a": values["current"],
            "faults": "|".join(values["faults"]),
        })
        if values["faults"]:
            tripped.append(f"{channel}: {', '.join(values['faults'])}")

    if tripped:
        raise TripError("; ".join(tripped))

    return reading, now


def _daq_channel_for(ctx, channel):
    """u200 -> 111, u201 -> 112, ...  the wiring the old electrical test uses."""
    return int(ctx.cfg["iseg_ramp_daq_first_channel"]) + ctx.channels.index(channel)


def _daq_sample(ctx, phase, started_at, snmp_reading):
    """One 34970A scan of all eight HV channels, paired with the SNMP values.

    Recorded, not judged.  Both numbers are written down as they are measured
    -- the raw meter volts and the module's own reading -- along with the
    ratio the pair implies.  Nothing is converted through a configured divider
    ratio, because the stated 10M:500k (21.0) and the observed ~44 do not yet
    agree and picking one of them would bake the disagreement into the data.
    """
    first = int(ctx.cfg["iseg_ramp_daq_first_channel"])
    volts = ctx.daq().read_scan(first, first + len(ctx.channels) - 1)
    timestamp = datetime.now().isoformat(timespec="milliseconds")
    elapsed = time.monotonic() - started_at

    for index, channel in enumerate(ctx.channels):
        if index >= len(volts):
            break
        raw = volts[index]
        snmp_v = abs(snmp_reading[channel]["voltage"]) if channel in snmp_reading else float("nan")
        ctx.daq_samples.append({
            "phase": phase,
            "timestamp": timestamp,
            "elapsed_s": round(elapsed, 3),
            "channel": channel,
            "daq_channel": first + index,
            "daq_voltage_v": raw,
            "snmp_hv_v": snmp_v,
            "implied_ratio": (snmp_v / raw) if raw else float("nan"),
        })


def _progress(ctx, phase, reading, elapsed):
    voltages = [abs(v["voltage"]) for v in reading.values()]
    currents = [abs(v["current"]) for v in reading.values()]
    print(
        f"\r    {phase}: t={elapsed:6.1f} s  |V| {min(voltages):7.1f} .. "
        f"{max(voltages):7.1f} V   |I| max {max(currents) * 1e6:6.2f} uA ",
        end="", flush=True,
    )


def _ramp_to(ctx, phase, target_magnitude_v, timeout_s):
    """Sets every channel to the target and samples until they all get there."""
    tolerance = float(ctx.cfg["iseg_ramp_reach_tolerance_v"])
    interval = float(ctx.cfg["iseg_ramp_sample_interval_s"])

    for channel in ctx.channels:
        ctx.mpod.set_voltage(channel, target_magnitude_v)

    started_at = time.monotonic()
    next_daq = started_at if ctx.cfg["iseg_ramp_daq_enabled"] else None
    while True:
        reading, now = _sample(ctx, phase, started_at, target_magnitude_v)
        elapsed = now - started_at
        _progress(ctx, phase, reading, elapsed)

        if next_daq is not None and now >= next_daq:
            print()
            _daq_sample(ctx, phase, started_at, reading)
            next_daq = time.monotonic() + float(ctx.cfg["iseg_ramp_daq_interval_s"])

        reached = all(
            abs(abs(values["voltage"]) - target_magnitude_v) <= tolerance
            for values in reading.values()
        )
        if reached:
            print()
            return elapsed

        if elapsed > timeout_s:
            print()
            worst = min(abs(v["voltage"]) for v in reading.values())
            raise TripError(
                f"{phase}: {target_magnitude_v:g} V not reached within "
                f"{timeout_s:.0f} s (lowest channel {worst:.1f} V)"
            )

        time.sleep(interval)


def _hold(ctx, seconds, target_magnitude_v):
    started_at = time.monotonic()
    interval = float(ctx.cfg["iseg_ramp_sample_interval_s"])
    next_daq = started_at if ctx.cfg["iseg_ramp_daq_enabled"] else None
    while time.monotonic() - started_at < seconds:
        reading, now = _sample(ctx, "HOLD", started_at, target_magnitude_v)
        _progress(ctx, "HOLD", reading, now - started_at)
        if next_daq is not None and now >= next_daq:
            print()
            _daq_sample(ctx, "HOLD", started_at, reading)
            next_daq = time.monotonic() + float(ctx.cfg["iseg_ramp_daq_interval_s"])
        time.sleep(interval)
    print()
    return time.monotonic() - started_at


def _fit_ramp_rate(samples, phase, channel, target_magnitude_v):
    """Least-squares |V| against t over the 20%-80% part of the ramp."""
    low, high = 0.2 * target_magnitude_v, 0.8 * target_magnitude_v
    points = [
        (row["elapsed_s"], abs(row["voltage_v"]))
        for row in samples
        if row["phase"] == phase and row["channel"] == channel
        and low <= abs(row["voltage_v"]) <= high
    ]
    if len(points) < 3:
        return float("nan")

    n = len(points)
    mean_t = sum(t for t, _ in points) / n
    mean_v = sum(v for _, v in points) / n
    denominator = sum((t - mean_t) ** 2 for t, _ in points)
    if denominator == 0:
        return float("nan")
    slope = sum((t - mean_t) * (v - mean_v) for t, v in points) / denominator
    return abs(slope)


def hv_ramp_test(ctx):
    """0 -> target @ rate, hold, target -> 0.  All 8 channels together."""
    cfg = ctx.cfg
    target = float(cfg["iseg_ramp_target_voltage"])
    rate = float(cfg["iseg_ramp_rate"])
    hold_seconds = float(cfg["iseg_ramp_hold_seconds"])

    expected_seconds = target / rate if rate > 0 else 0.0
    timeout = expected_seconds * float(cfg["iseg_ramp_timeout_factor"]) + float(
        cfg["iseg_ramp_timeout_margin_s"]
    )

    ctx.console.info(
        f"HV Ramp Test: target {target:g} V, ramp rate {rate:g} V/s, "
        f"hold {hold_seconds:g} s, {len(ctx.channels)} channels together"
    )

    # A channel that tripped on an earlier run will not ramp until its events
    # are cleared.
    for channel in ctx.channels:
        try:
            ctx.mpod.clear_channel_events(channel)
        except SNMPError:
            pass

    commanded_percent = ctx.mpod.set_ramp_rate(ctx.module_id, rate, ctx.nominal_voltage)
    percent = ctx.mpod.get_module_ramp_percent(ctx.module_id)
    readback = ctx.mpod.get_ramp_rate(ctx.channels[0])
    ctx.console.info(
        f"Ramp rate set to {rate:g} V/s = {commanded_percent:.3f} % of full "
        f"scale; module reads {percent:.3f} %, channels read {readback:.2f} V/s"
    )
    ctx.data["ramp_rate_commanded_v_per_s"] = rate
    ctx.data["ramp_rate_commanded_percent"] = commanded_percent
    ctx.data["ramp_rate_readback_v_per_s"] = readback
    ctx.data["ramp_rate_module_percent"] = percent

    tolerance = float(cfg["iseg_ramp_rate_readback_tolerance_fraction"])
    if rate > 0 and abs(readback - rate) > tolerance * rate:
        return False, (
            f"ramp rate readback {readback:.2f} V/s does not match the "
            f"commanded {rate:g} V/s ({commanded_percent:.3f} %, module reads "
            f"{percent:.3f} %) -- refusing to ramp"
        )

    for channel in ctx.channels:
        ctx.mpod.set_current(channel, float(cfg["iseg_current_limit_a"]))
        ctx.mpod.set_voltage(channel, 0.0)

    for channel in ctx.channels:
        ctx.mpod.channel_on(channel)

    try:
        up_seconds = _ramp_to(ctx, "UP", target, timeout)
        held = _hold(ctx, hold_seconds, target)
        down_seconds = _ramp_to(ctx, "DOWN", 0.0, timeout)
    finally:
        # Whatever happened, the channels do not stay on.
        try:
            ctx.mpod.all_off(ctx.channels)
        except Exception:
            print(traceback.format_exc())

    rates = {}
    for channel in ctx.channels:
        rates[channel] = {
            "up_v_per_s": _fit_ramp_rate(ctx.ramp_samples, "UP", channel, target),
            "down_v_per_s": _fit_ramp_rate(ctx.ramp_samples, "DOWN", channel, target),
        }
    ctx.data["measured_ramp_rates"] = rates
    ctx.data["ramp_seconds"] = {"up": up_seconds, "hold": held, "down": down_seconds}

    daq_summary = _summarise_daq(ctx)
    if daq_summary:
        nominal = (
            float(cfg["iseg_ramp_divider_top_ohm"])
            + float(cfg["iseg_ramp_divider_bottom_ohm"])
        ) / float(cfg["iseg_ramp_divider_bottom_ohm"])
        ctx.data["daq_observation"] = {
            "nominal_divider_ratio": nominal,
            "per_channel": daq_summary,
        }

    measured = [
        value for channel_rates in rates.values() for value in channel_rates.values()
        if not math.isnan(value)
    ]
    if measured:
        summary = f"up {up_seconds:.0f} s, down {down_seconds:.0f} s, measured ramp {min(measured):.1f}-{max(measured):.1f} V/s"
    else:
        summary = f"up {up_seconds:.0f} s, down {down_seconds:.0f} s"

    if daq_summary:
        # Recorded, not graded -- see _daq_sample.
        means = [d["mean_ratio"] for d in daq_summary.values()]
        summary += (
            f"; DAQ observed ratio {min(means):.2f}-{max(means):.2f} "
            f"(nominal {nominal:.1f}, not graded)"
        )

    if cfg["iseg_grade_ramp_rate"] and measured:
        tolerance = float(cfg["iseg_ramp_rate_tolerance_fraction"])
        out_of_range = [value for value in measured if abs(value - rate) > rate * tolerance]
        if out_of_range:
            return False, summary + f" -- outside +/-{tolerance:.0%} of {rate:g} V/s"

    return True, summary


def _stage_channel(ctx, stage):
    """Which 34970A channel is watching this potentiometer.

    Whatever the operator typed wins; the config default is only a
    convenience, and there is no fallback beyond it -- measuring the wrong
    channel would produce a confident, wrong answer.
    """
    answer = ctx.answers.get(f"{stage.key}_daq_channel", "").strip()
    if not answer:
        default = ctx.cfg.get(f"iseg_limit_{stage.key}_daq_channel")
        if default in (None, "", 0):
            raise ValueError(
                f"no DAQ channel for {stage.label}: nothing was typed and "
                f"iseg_limit_{stage.key}_daq_channel is not set in config.json"
            )
        answer = str(default)

    channel = int(answer)
    if not (hv_limit_adjust.MIN_CHANNEL <= channel <= hv_limit_adjust.MAX_CHANNEL):
        raise ValueError(
            f"{stage.label} channel {channel} is outside "
            f"{hv_limit_adjust.MIN_CHANNEL}-{hv_limit_adjust.MAX_CHANNEL}"
        )
    return channel + hv_limit_adjust.SLOT_OFFSET


def limit_stage(ctx, stage):
    """One potentiometer adjustment.

    Judged by the instrument, not by the operator -- the tester turns the pot,
    the software decides whether the voltage landed in the window -- so this is
    an AUTO step even though a person is doing the turning.
    """
    try:
        channel = _stage_channel(ctx, stage)
    except ValueError as exc:
        return False, str(exc)

    daq = ctx.daq()
    if hasattr(daq, "set_target"):          # the --dry-run simulator
        daq.set_target(stage.target_v)

    ctx.console.info(
        f"{stage.label}: DAQ channel {channel} reads "
        f"{daq.read_voltage(channel):.6f} V before adjustment"
    )
    good, summary = hv_limit_adjust.monitor_stage(
        daq.read_voltage, stage, channel,
    )
    ctx.data.setdefault("limit_adjustment", {})[stage.key] = summary

    if summary["result"] == "stopped by the tester":
        return False, f"{stage.label} adjustment stopped by the tester"

    return good, (
        f"{stage.label} = {summary['value_v']:.6f} V "
        f"(target {stage.target_v:g} V, window {stage.window_text()}) "
        f"-- {summary['result']}"
    )


def _summarise_daq(ctx):
    """Per channel: the ratio the measurements imply.  No verdict.

    Only points above 50 V are used -- near zero the ratio is the quotient of
    two small numbers and means nothing.
    """
    if not ctx.daq_samples:
        return {}

    summary = {}
    for channel in ctx.channels:
        ratios = [
            r["implied_ratio"] for r in ctx.daq_samples
            if r["channel"] == channel
            and not math.isnan(r["implied_ratio"])
            and abs(r["snmp_hv_v"]) > 50.0
        ]
        if not ratios:
            continue
        summary[channel] = {
            "channel": channel,
            "points": len(ratios),
            "mean_ratio": sum(ratios) / len(ratios),
            "min_ratio": min(ratios),
            "max_ratio": max(ratios),
        }
    return summary


def hv_limit_test(ctx):
    """Reads back what the potentiometers ended up at, over SNMP.

    An independent confirmation of the adjustment: the 34970A measured the
    control voltage, the MPOD reports the percentage that resulted from it.
    """
    cfg = ctx.cfg
    percent = ctx.mpod.hardware_limit_voltage_percent(ctx.module_id)
    current_percent = ctx.mpod.hardware_limit_current_percent(ctx.module_id)
    nominal_current = ctx.mpod.nominal_current(ctx.channels[0])
    limit_v = abs(ctx.nominal_voltage) * percent / 100.0
    limit_a = abs(nominal_current) * current_percent / 100.0

    ctx.data["hardware_limit_voltage_percent"] = percent
    ctx.data["hardware_limit_current_percent"] = current_percent
    ctx.data["hardware_limit_voltage_v"] = limit_v
    ctx.data["hardware_limit_current_a"] = limit_a

    detail = (
        f"voltage pot {percent:.2f} % = {limit_v:.0f} V, "
        f"current pot {current_percent:.2f} % = {limit_a * 1e3:.3f} mA"
    )

    if not cfg["iseg_limit_test_enabled"]:
        return True, detail + " -- TBD, not graded"

    tolerance = float(cfg["iseg_limit_percent_tolerance"])
    problems = []
    for label, measured, expected_key in (
        ("voltage", percent, "iseg_limit_expected_voltage_percent"),
        ("current", current_percent, "iseg_limit_expected_current_percent"),
    ):
        expected = float(cfg[expected_key])
        if abs(measured - expected) > tolerance:
            problems.append(
                f"{label} pot {measured:.2f} %, expected {expected:.2f} % "
                f"+/-{tolerance:g}"
            )

    if problems:
        return False, detail + " -- " + "; ".join(problems)

    return True, detail


# ----------------------------------------------------------------------
# The flow
# ----------------------------------------------------------------------
# What each configuration has fitted.  Every expectation is derived from these
# two facts, measured on ma2 across all three configurations (2026-08-25):
#
#     jumper  kit  | bit8  bit10  bit11  bit12 | LED
#     -------------+---------------------------+-----
#       in    in   |  set   set   clear   set  | ON
#       in    out  | clear  set   clear  clear | OFF
#       out   in   |  set  CLEAR   SET   clear | OFF
#
#     moduleSafetyLoopIsGood(10) = the JUMPER.  Pulling it also latches
#                                  moduleIsEventActive(11).
#     moduleIsGood(12)           = jumper AND test kit -- exactly the LED
#
# So the LED does have an automatic cross-check after all.  Getting here took
# two wrong guesses; see the devlog before changing any of it.
#
# Pages of GUI/ISEG_Qualification_Protocol.pdf, exported to
# GUI/protocol_pngs/<page>.png.  Named rather than written as bare numbers at
# the call sites, so re-ordering the deck is one edit here.
PAGE_TITLE = 1              # ISEG Module Qualification Standard
PAGE_GAUNTLET = 2           # The Verification Gauntlet -- the 5 stages
PAGE_UNBOXING = 3           # Initial Unboxing & Documentation Match
PAGE_INTEGRITY = 4          # Hardware Integrity Check
PAGE_SEATING = 5            # Crate De-Energization & Module Seating
PAGE_HANDSHAKE = 6          # System Initialization & Handshake (LED on, SN read)
PAGE_SAFETY_01 = 7          # Safety Test 01 -- remove the jumper
PAGE_SAFETY_01_LED = 9      # ...at this condition the LED is off
PAGE_SAFETY_02 = 10         # Safety Test 02 -- remove the test components
PAGE_SAFETY_02_LED = 12     # ...at this condition the LED is off
PAGE_RESTORE = 13           # Restoring Normal Configuration & Arming
PAGE_POT_SETUP = 17         # Physical Limitation Calibration Setup -- the pots
PAGE_POT_TARGETS = 18       # The Limitation Translation Matrix -- the numbers
PAGE_TEARDOWN = 19          # Teardown & Safe Repackaging
PAGE_INTERLOCK = 20         # The Safety Loop Interlock Matrix -- all three states

# The order follows ISEG_Qualification_Protocol.pdf, which the operator has in
# front of them: its "Safety Test 01" pulls the jumper (test kit still fitted)
# and its "Safety Test 02" pulls the test components (jumper still fitted).
# Only the order changed -- nothing reads these entries positionally, and every
# expectation is still derived from jumper_in / test_kit_in.
JUMPER_CONFIGS = [
    {
        "key": "test_kit_only",
        "title": "Test Kit Only Test",
        "prompt": ("Remove the module.\nRemove the jumper.\n"
                   "Install the test kit.\nReinsert the module."),
        "jumper_in": False,
        "test_kit_in": True,
        "page": PAGE_SAFETY_01,
        "led_page": PAGE_SAFETY_01_LED,
    },
    {
        "key": "jumper_only",
        "title": "Jumper Only Test",
        "prompt": ("Remove the module.\nRemove the test kit.\n"
                   "Keep the jumper installed.\nReinsert the module."),
        "jumper_in": True,
        "test_kit_in": False,
        "page": PAGE_SAFETY_02,
        "led_page": PAGE_SAFETY_02_LED,
    },
    {
        "key": "restore",
        "title": "Restore Normal Configuration",
        "prompt": ("Remove the module.\nInstall both the test kit and the "
                   "jumper.\nReinsert the module."),
        "jumper_in": True,
        "test_kit_in": True,
        "page": PAGE_RESTORE,
        "led_page": PAGE_INTERLOCK,
    },
]


def power_down_steps(ctx, key_prefix, label):
    """Outputs off, then the operator kills the AC mains, then verify.

    The label goes into the step titles so the four power cycles stay apart in
    the terminal output and get columns of their own in the spreadsheet.
    """
    steps = [
        Step(AUTO, f"Power OFF MPOD [{label}]", action=outputs_off,
             key=f"{key_prefix}_outputs_off"),
    ]

    if ctx.cfg["iseg_power_cycle_mode"] != "snmp":
        steps.append(
            Step(USER, f"Power OFF MPOD -- AC mains [{label}]",
                 "Switch OFF the AC mains switch on the rear panel of the "
                 "MPOD crate.\n\niseg modules are not hot-swappable.")
        )

    # Verified either way: the bay must be proven dead before anyone touches
    # a module, whether the software or a person switched it off.
    steps.append(
        Step(AUTO, f"Confirm Crate Is Off [{label}]", action=confirm_crate_dead,
             severity=UNSAFE, key=f"{key_prefix}_confirm_off")
    )
    return steps


def power_up_steps(ctx, key_prefix, label):
    steps = []
    if ctx.cfg["iseg_power_cycle_mode"] != "snmp":
        steps.append(
            Step(USER, f"Power ON MPOD -- AC mains [{label}]",
                 "Switch ON the AC mains switch on the rear panel of the MPOD crate.")
        )
    steps.append(
        Step(AUTO, f"Power ON MPOD [{label}]", action=wait_for_crate,
             key=f"{key_prefix}_power_on")
    )
    return steps


def jumper_steps(ctx, config):
    """One of the three safety-jumper configurations."""
    # The LED needs both; the module status bits only see the test kit; the
    # safety loop sees neither and should always read closed.
    led_on = config["jumper_in"] and config["test_kit_in"]
    led_state = "ON" if led_on else "OFF"
    return (
        power_down_steps(ctx, config["key"], config["title"])
        + [Step(USER, config["title"], config["prompt"], image=config["page"])]
        + power_up_steps(ctx, config["key"], config["title"])
        + [
            Step(AUTO, f"Read Safety Loop ({config['key']})",
                 action=lambda c, expect=config["jumper_in"]: check_safety_loop(c, expect),
                 key=f"{config['key']}_safety_loop"),
            Step(AUTO, f"Read Module State ({config['key']})",
                 action=lambda c, expect=led_on: check_module_good(c, expect),
                 key=f"{config['key']}_module_good"),
            Step(CHECK, f"Check Green LED ({config['key']})",
                 f"Look at the green LED on Slot {ctx.slot}.\n"
                 f"Is the green LED {led_state}?\n\n"
                 "moduleIsGood is read separately and must agree -- look at "
                 "the module, do not assume.",
                 expect=f"Green LED = {led_state}",
                 key=f"{config['key']}_led", image=config["led_page"]),
        ]
    )


def build_flow(ctx):
    steps = []

    # 1. Visual inspection
    steps += [
        Step(USER, "Open Box", "Open the box and take out the HV module.",
             image=PAGE_UNBOXING),
        Step(CHECK, "Check Components",
             "Visually inspect the module and its components.\n"
             "Are there any visible defects?",
             expect="No visible damage and no missing components.",
             key="visual_inspection", image=PAGE_INTEGRITY),
        Step(USER, "Enter Serial Number",
             "Type the serial number printed on the module label.",
             input_key="expected_serial", image=PAGE_UNBOXING),
        Step(USER, "Install Test Kit and Jumper",
             "Install the test kit and the safety jumper on the module.",
             image=PAGE_RESTORE),
    ]

    # 2. Insert the module
    steps += power_down_steps(ctx, "insert", "Insert")
    steps += [
        Step(USER, "Insert Module into Test Slot",
             f"The crate is powered down and the module bay has been confirmed "
             f"dead.\n\nInsert the module into Slot {ctx.slot}.",
             image=PAGE_SEATING),
    ]
    steps += power_up_steps(ctx, "insert", "Insert")
    steps += [
        Step(AUTO, "Read Serial Number", action=read_serial, key="serial_number"),
        Step(AUTO, "Check Module Health", action=check_module_health, key="module_health"),
        Step(AUTO, "Read Safety Loop", action=lambda c: check_safety_loop(c, True),
             key="initial_safety_loop"),
        Step(AUTO, "Read Module State", action=lambda c: check_module_good(c, True),
             key="initial_module_good"),
        Step(CHECK, "Check Green LED",
             f"Look at the green LED on Slot {ctx.slot}.\nIs the green LED ON?",
             expect="Green LED = ON", key="initial_led", image=PAGE_HANDSHAKE),
    ]

    # 3. Safety jumper loop test
    for config in JUMPER_CONFIGS:
        steps += jumper_steps(ctx, config)

    # 4. HV ramp test
    steps.append(
        Step(AUTO, "HV Ramp Test", action=hv_ramp_test, severity=UNSAFE, key="hv_ramp")
    )

    # 5. HV limitation test -- potentiometer adjustment, then an SNMP readback
    if ctx.cfg["iseg_limit_test_enabled"]:
        for stage in (VMAX_STAGE, IMAX_STAGE):
            default = ctx.cfg.get(f"iseg_limit_{stage.key}_daq_channel")
            default_text = (
                f"\n\nPress Enter for the configured default ({default})."
                if default not in (None, "", 0) else
                "\n\nNote: 111-118 are the eight HV channel outputs, so this "
                "is not one of them."
            )
            steps += [
                Step(USER, f"Select {stage.label} DAQ Channel",
                     f"Which 34970A slot-1 channel "
                     f"({hv_limit_adjust.MIN_CHANNEL}-"
                     f"{hv_limit_adjust.MAX_CHANNEL}) is wired to "
                     f"{stage.what} output?" + default_text,
                     input_key=f"{stage.key}_daq_channel",
                     allow_empty=default not in (None, "", 0),
                     # Vmax gets the page showing where the pots are, Imax the
                     # one with the target numbers; by then the tester has
                     # already seen the setup page once.
                     image=PAGE_POT_SETUP if stage is VMAX_STAGE else PAGE_POT_TARGETS),
                Step(AUTO, f"{stage.label} Limit Adjustment",
                     action=lambda c, st=stage: limit_stage(c, st),
                     key=f"limit_{stage.key}"),
            ]

    steps.append(
        Step(AUTO, "HV Limit Readback", action=hv_limit_test, severity=UNSAFE,
             key="hv_limit", informational=not ctx.cfg["iseg_limit_test_enabled"])
    )

    return steps


# ----------------------------------------------------------------------
# Output files
# ----------------------------------------------------------------------
def write_ramp_csv(ctx):
    if not ctx.ramp_samples:
        return None
    path = os.path.join(ctx.make_results_dir(), f"hv_ramp_SN{ctx.serial()}.csv")
    fields = ["phase", "timestamp", "elapsed_s", "channel", "target_voltage_v",
              "voltage_v", "current_a", "faults"]
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(ctx.ramp_samples)
    return path


def write_daq_csv(ctx):
    if not ctx.daq_samples:
        return None
    path = os.path.join(ctx.make_results_dir(), f"hv_ramp_daq_SN{ctx.serial()}.csv")
    fields = ["phase", "timestamp", "elapsed_s", "channel", "daq_channel",
              "daq_voltage_v", "snmp_hv_v", "implied_ratio"]
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(ctx.daq_samples)
    return path


def plot_ramp(ctx):
    if not ctx.ramp_samples:
        return []

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        ctx.log("matplotlib is not available -- skipping the ramp plots")
        return []

    # Phases are recorded with their own clocks; lay them end to end for the plot
    offsets, running = {}, 0.0
    for phase in ("UP", "HOLD", "DOWN"):
        rows = [r for r in ctx.ramp_samples if r["phase"] == phase]
        if not rows:
            continue
        offsets[phase] = running
        running += max(r["elapsed_s"] for r in rows)

    series = {}
    for row in ctx.ramp_samples:
        if row["phase"] not in offsets:
            continue
        entry = series.setdefault(row["channel"], {"t": [], "v": [], "i": []})
        entry["t"].append(offsets[row["phase"]] + row["elapsed_s"])
        entry["v"].append(abs(row["voltage_v"]))
        entry["i"].append(abs(row["current_a"]) * 1e6)

    directory = ctx.make_results_dir()
    paths = []
    for quantity, label, filename in (
        ("v", "|Voltage| [V]", "hv_ramp_voltage.png"),
        ("i", "|Current| [uA]", "hv_ramp_current.png"),
    ):
        figure, axes = plt.subplots(figsize=(10, 6))
        for channel in sorted(series):
            axes.plot(series[channel]["t"], series[channel][quantity],
                      label=channel.upper(), linewidth=1.0)

        axes.set_xlabel("Time [s]")
        axes.set_ylabel(label)
        axes.set_title(
            f"HV Ramp Test -- {ctx.module.model if ctx.module else ''} "
            f"SN {ctx.serial()} -- {ctx.cfg['iseg_ramp_rate']:g} V/s to "
            f"{ctx.cfg['iseg_ramp_target_voltage']:g} V"
        )
        axes.grid(True, alpha=0.3)
        axes.legend(ncol=4, fontsize="x-small")
        path = os.path.join(directory, filename)
        figure.tight_layout()
        figure.savefig(path, dpi=120)
        plt.close(figure)
        paths.append(path)

    if ctx.daq_samples:
        # Two panels because the two things worth seeing have different units:
        # what the meter actually read, and what ratio that implies.  Nothing
        # is converted or judged -- see _daq_sample.
        figure, (top, bottom) = plt.subplots(2, 1, figsize=(10, 9))

        for channel in sorted(ctx.channels):
            rows = [r for r in ctx.daq_samples if r["channel"] == channel]
            if not rows:
                continue
            times, ratios, hv = [], [], []
            for row in rows:
                if row["phase"] not in offsets:
                    continue
                times.append(offsets[row["phase"]] + row["elapsed_s"])
                if abs(row["snmp_hv_v"]) > 50.0 and not math.isnan(row["implied_ratio"]):
                    ratios.append((abs(row["snmp_hv_v"]), row["implied_ratio"]))
                hv.append(abs(row["daq_voltage_v"]))
            top.plot(times, hv, marker="o", markersize=3, linewidth=0.8,
                     label=channel.upper())
            if ratios:
                bottom.plot([r[0] for r in ratios], [r[1] for r in ratios],
                            marker="o", markersize=3, linewidth=0.8,
                            label=channel.upper())

        nominal = (
            float(ctx.cfg["iseg_ramp_divider_top_ohm"])
            + float(ctx.cfg["iseg_ramp_divider_bottom_ohm"])
        ) / float(ctx.cfg["iseg_ramp_divider_bottom_ohm"])
        bottom.axhline(nominal, color="red", linewidth=1.0, linestyle="--",
                       label=f"nominal {nominal:.1f}")

        top.set_xlabel("Time [s]")
        top.set_ylabel("34970A reading [V]")
        top.set_title(f"34970A through the HV divider -- SN {ctx.serial()} (recorded, not graded)")
        top.grid(True, alpha=0.3)
        top.legend(ncol=4, fontsize="small")

        bottom.set_xlabel("Module reported |Voltage| [V]")
        bottom.set_ylabel("implied ratio  (module / meter)")
        bottom.set_title("Ratio the measurements imply, against the nominal 10M:500k")
        bottom.grid(True, alpha=0.3)
        bottom.legend(ncol=4, fontsize="small")

        path = os.path.join(directory, "hv_ramp_daq.png")
        figure.tight_layout()
        figure.savefig(path, dpi=120)
        plt.close(figure)
        paths.append(path)

    return paths


def write_results_csv(ctx, results, overall):
    """The step-by-step verdict as a flat CSV, next to the raw sample data."""
    path = os.path.join(ctx.make_results_dir(),
                        f"hv_module_test_SN{ctx.serial()}_results.csv")
    module = ctx.data.get("module", {})
    fields = ["step", "kind", "title", "result", "detail", "timestamp",
              "serial_number", "model", "firmware", "slot", "test_name", "overall"]
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for index, step_result in enumerate(results, start=1):
            writer.writerow({
                "step": index,
                "kind": step_result.kind,
                "title": step_result.title,
                "result": step_result.verdict(),
                "detail": step_result.detail,
                "timestamp": step_result.timestamp,
                "serial_number": module.get("serial_number", ""),
                "model": module.get("model", ""),
                "firmware": module.get("firmware", ""),
                "slot": ctx.slot,
                "test_name": getattr(ctx.session, "test_name", ""),
                "overall": "Pass" if overall else "Fail",
            })
    return path


def write_json(ctx, results, overall):
    path = os.path.join(ctx.make_results_dir(), f"hv_module_test_SN{ctx.serial()}.json")
    payload = {
        "test_name": getattr(ctx.session, "test_name", "standalone"),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "crate_ip": ctx.cfg["iseg_ip"],
        "slot": ctx.slot,
        "overall": "Pass" if overall else "Fail",
        "dry_run": ctx.dry_run,
        "config": {k: ctx.cfg[k] for k in sorted(ctx.cfg)},
        "operator_input": ctx.answers,
        "module": ctx.data.get("module", {}),
        "measurements": {k: v for k, v in ctx.data.items() if k != "module"},
        "steps": [
            {"key": r.key, "kind": r.kind, "title": r.title,
             "result": r.verdict(), "detail": r.detail, "timestamp": r.timestamp}
            for r in results
        ],
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=4, default=str)
    return path


def write_spreadsheet(ctx, results, overall):
    """One row per run in a workbook of this item's own.

    Columns are discovered from the results, so adding a step to the flow does
    not break an existing workbook -- new columns are appended on the right.
    """
    try:
        import openpyxl
    except ImportError:
        ctx.log("openpyxl is not available -- skipping the spreadsheet")
        return None

    os.makedirs(RESULTS_ROOT, exist_ok=True)
    name = ("hv_module_test_dry_run.xlsx" if ctx.dry_run
            else "hv_module_test_results.xlsx")
    path = os.path.join(RESULTS_ROOT, name)

    fixed = ["Test Name", "Date", "Time", "Slot", "Model", "Serial Number",
             "Firmware", "Polarity", "Overall"]
    values = {
        "Test Name": getattr(ctx.session, "test_name", "standalone"),
        "Date": datetime.today().strftime("%m/%d/%Y"),
        "Time": datetime.today().strftime("%I:%M:%S %p"),
        "Slot": ctx.slot,
        "Model": ctx.data.get("module", {}).get("model", ""),
        "Serial Number": ctx.data.get("module", {}).get("serial_number", ""),
        "Firmware": ctx.data.get("module", {}).get("firmware", ""),
        "Polarity": ctx.data.get("polarity", ""),
        "Overall": "PASS" if overall else "FAIL",
    }
    for step_result in results:
        values[step_result.title] = step_result.verdict()

    if os.path.isfile(path):
        workbook = openpyxl.load_workbook(path)
        sheet = workbook.active
        headers = [sheet.cell(row=1, column=c).value for c in range(1, sheet.max_column + 1)]
        headers = [h for h in headers if h]
    else:
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "HV Module Test"
        headers = []

    for header in fixed + [r.title for r in results]:
        if header not in headers:
            headers.append(header)
    for column, header in enumerate(headers, start=1):
        cell = sheet.cell(row=1, column=column, value=header)
        cell.font = openpyxl.styles.Font(bold=True)

    row = sheet.max_row + 1 if sheet.max_row > 1 else 2
    red = openpyxl.styles.Font(color="FF0000")
    green = openpyxl.styles.Font(color="009900")
    for column, header in enumerate(headers, start=1):
        value = values.get(header, "")
        cell = sheet.cell(row=row, column=column, value=value)
        if value == "FAIL":
            cell.font = red
        elif value == "PASS":
            cell.font = green

    sheet.freeze_panes = sheet.cell(row=2, column=1)
    workbook.save(path)
    return path


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------
def run(session, console=None, mpod=None, dry_run=False, daq=None):
    cfg = _cfg(session.json_data)

    if mpod is None:
        mpod = session.mpod
    if console is None:
        if dry_run:
            console = AutoConsole()
        else:
            # Resolved once, here, so an operator on a machine with no desktop
            # is told at start-up instead of finding out at step 16.
            console = Console(popup=popups.make_popup(cfg, log=lambda m: print(f"    {m}")))

    ctx = Context(session, mpod, console, cfg, dry_run=dry_run)
    if daq is not None:
        ctx._daq = daq
    runner = FlowRunner(
        console, ctx,
        on_unsafe=lambda: safe_state(ctx),
        log=ctx.log,
    )
    ctx.runner = runner

    console.header(
        "DUNE WIEC HV MODULE TEST",
        f"Crate {cfg['iseg_ip']}, slot {cfg['iseg_slot']}.\n"
        f"Power cycling: {cfg['iseg_power_cycle_mode']}.\n"
        "The module is brought to 0 V and the crate switched off if anything "
        "goes wrong, including Ctrl-C.",
    )

    # The two title pages are not steps, so they are shown here rather than
    # through Step.image.
    console.show_page("ISEG Module Qualification Standard", PAGE_TITLE)
    console.show_page("The Verification Gauntlet", PAGE_GAUNTLET)

    aborted = False
    try:
        runner.run(build_flow(ctx))
    except AbortFlow as exc:
        aborted = True
        ctx.log(f"Flow stopped: {exc}")
    except KeyboardInterrupt:
        aborted = True
        ctx.log("Interrupted by the operator -- bringing the module to a safe state")
        safe_state(ctx)
        raise
    finally:
        try:
            safe_state(ctx)
        except Exception:
            print(traceback.format_exc())
        try:
            ctx.close_daq()
        except Exception:
            print(traceback.format_exc())

    overall = runner.verdict() and not aborted

    csv_path = write_ramp_csv(ctx)
    daq_csv_path = write_daq_csv(ctx)
    plot_paths = plot_ramp(ctx)
    results_csv_path = write_results_csv(ctx, runner.results, overall)
    json_path = write_json(ctx, runner.results, overall)
    sheet_path = write_spreadsheet(ctx, runner.results, overall)

    # The report is built from what was just written, so it is never out of
    # step with the data; hv_module_report can rebuild it later from the same
    # files if the template changes.
    try:
        from . import hv_module_report
        report_path = hv_module_report.write_report(ctx.make_results_dir())
    except Exception as exc:
        report_path = None
        ctx.log(f"Could not build the HTML report: {type(exc).__name__}: {exc}")

    print()
    print("=" * 70)
    print(f" HV MODULE TEST SUMMARY -- SN {ctx.serial()}")
    print("=" * 70)
    for line in runner.summary_lines():
        print(line)
    print("=" * 70)
    print(f" OVERALL: {'PASS' if overall else 'FAIL'}")
    if runner.abort_reason:
        print(f" Stopped: {runner.abort_reason}")
    for label, path in (("Report", report_path), ("Results CSV", results_csv_path),
                        ("Ramp CSV", csv_path), ("DAQ CSV", daq_csv_path),
                        ("JSON", json_path), ("Spreadsheet", sheet_path)):
        if path:
            print(f" {label:<12}: {path}")
    for path in plot_paths:
        print(f" {'Plot':<12}: {path}")
    print("=" * 70)

    # Last, after the paths are on screen: the module still has to come out of
    # the crate and go back in its box, and that is the page describing it.
    console.show_page("Teardown & Safe Repackaging", PAGE_TEARDOWN)

    if session is not None:
        session.datastore.setdefault("hv_module_test", {})
        session.datastore["hv_module_test"] = {
            "overall": "Pass" if overall else "Fail",
            "results_dir": ctx.results_dir,
            "steps": {r.key: r.verdict() for r in runner.results},
            **ctx.data,
        }

    return overall


# ----------------------------------------------------------------------
# Simulated crate, for --dry-run
# ----------------------------------------------------------------------
class SimulatedMPOD:
    """Enough of IsegMPOD to walk the flow with no hardware attached."""

    def __init__(self, slot=2, serial_number="DRYRUN123"):
        self.slot = slot
        self.module_id = f"ma{slot}"
        self.channels = [f"u{slot * 100 + i}" for i in range(8)]
        self.serial_number = serial_number
        self.alive = True
        self.on = True
        self.setpoint = {c: 0.0 for c in self.channels}
        self.actual = {c: 0.0 for c in self.channels}
        self.rate_v_per_s = 100.0
        self.last_update = time.monotonic()
        # What is fitted each time the flow asks: the module as inserted, then
        # one entry per jumper configuration.  Derived from JUMPER_CONFIGS
        # rather than written out, because these used to be two hand-written
        # lists whose positions had to match the configuration order -- and
        # re-ordering the configurations to follow the protocol deck silently
        # made both of them wrong.  The bits themselves follow the hardware:
        # the loop is the jumper, moduleIsGood is jumper AND test kit.
        fitted = [(True, True)] + [
            (config["jumper_in"], config["test_kit_in"]) for config in JUMPER_CONFIGS
        ]
        self.safety_loop_script = [jumper for jumper, _kit in fitted]
        self.module_good_script = [jumper and kit for jumper, kit in fitted]

    # -- crate
    def _advance(self):
        now = time.monotonic()
        step = self.rate_v_per_s * (now - self.last_update)
        self.last_update = now
        for channel in self.channels:
            error = self.setpoint[channel] - self.actual[channel]
            if abs(error) <= step:
                self.actual[channel] = self.setpoint[channel]
            else:
                self.actual[channel] += math.copysign(step, error)

    def is_alive(self, timeout_s=3.0):
        return self.alive

    def wait_until_alive(self, timeout_s=180.0, poll_s=3.0):
        self.alive = True
        return True

    def wait_until_dead(self, timeout_s=120.0, poll_s=3.0):
        self.alive = False
        return True

    def main_switch(self):
        return self.on

    def turn_on_crate(self):
        self.on = True

    def turn_off_crate(self):
        self.on = False

    def crate_serial(self):
        return "SIMULATED"

    # -- module
    def module_in_slot(self, slot):
        return ModuleInfo(
            module_id=self.module_id, slot=slot, vendor="iseg", model="E08F2",
            channel_count=8, serial_number=self.serial_number, firmware="07.52",
            description=f"iseg, E08F2, 08, {self.serial_number}, 07.52",
            channels=list(self.channels),
        )

    def module_health(self, module_id):
        return []

    def module_present(self, slot):
        return True

    def module_count(self):
        return 1

    def output_count(self):
        return 8

    def module_bay_powered(self):
        return self.on, "simulated crate"

    def safety_loop_closed(self, module_id):
        return self.safety_loop_script.pop(0) if self.safety_loop_script else True

    def module_good(self, module_id):
        """Replays what a good module reports, so a dry run passes."""
        good = self.module_good_script.pop(0) if self.module_good_script else True
        return good, (
            f"moduleIsGood={'set' if good else 'clear'}, "
            f"moduleIsNoSumError={'set' if good else 'clear'}"
        )

    def hardware_limit_voltage_percent(self, module_id):
        return 62.5

    def hardware_limit_current_percent(self, module_id):
        return 6.25

    def clear_channel_events(self, channel):
        pass

    # -- channels
    def nominal_voltage(self, channel):
        return -2000.0

    def nominal_current(self, channel):
        return 0.004

    def polarity(self, channel):
        return -1

    def set_ramp_rate(self, module_id, rate_v_per_s, nominal_voltage_v):
        self.rate_v_per_s = abs(rate_v_per_s)
        return 100.0 * self.rate_v_per_s / abs(nominal_voltage_v)

    def get_ramp_rate(self, channel):
        return self.rate_v_per_s

    def get_module_ramp_percent(self, module_id):
        return 100.0 * self.rate_v_per_s / 2000.0

    def set_voltage(self, channel, voltage):
        self.setpoint[channel] = abs(voltage)

    def set_current(self, channel, current):
        pass

    def channel_on(self, channel):
        pass

    def channel_off(self, channel):
        pass

    def all_off(self, channels):
        return []

    def read_all(self, channels):
        self._advance()
        return {
            channel: {"voltage": self.actual[channel], "current": -1e-7,
                      "faults": [], "ramping": False, "on": True}
            for channel in channels
        }

    def wait_until_zero(self, channels, timeout_s=300.0, poll_s=2.0):
        for channel in channels:
            self.setpoint[channel] = 0.0
            self.actual[channel] = 0.0
        return True


class SimulatedDAQ:
    """Walks towards each target so --dry-run exercises the monitor loop.

    Also fakes the HV divider channels, from the simulated crate's own
    voltages, so the ramp cross-check has something to compare.
    """

    def __init__(self, mpod=None, ratios=None):
        self.value = 0.0
        self.target = VMAX_STAGE.target_v
        self.mpod = mpod
        self.ratios = ratios or {}

    def idn(self):
        return "SIMULATED,34970A"

    def set_target(self, target_v):
        """The flow says which stage it is on -- guessing it from the value is
        what made the simulator abandon Vmax halfway through it."""
        self.target = target_v

    def read_voltage(self, channel):
        step = max(abs(self.target - self.value) * 0.5, 0.001)
        if self.value < self.target:
            self.value = min(self.target, self.value + step)
        else:
            self.value = max(self.target, self.value - step)
        return self.value

    def read_scan(self, first, last):
        """The HV divider channels, at the ~44 the July data actually shows."""
        if self.mpod is None:
            return [0.0] * (last - first + 1)
        values = []
        for index, channel in enumerate(self.mpod.channels[: last - first + 1]):
            hv = abs(self.mpod.actual.get(channel, 0.0))
            values.append(hv / (44.0 * (1.0 + 0.005 * (index - 3))))
        return values

    def close(self):
        pass


class _StandaloneSession:
    """A session stand-in for --dry-run, so no instrument is ever opened."""

    def __init__(self, config_file, test_name):
        with open(config_file) as handle:
            self.json_data = json.load(handle)
        self.test_name = test_name
        self.datastore = {}

    def log(self, message):
        print(f"HV Module Test --> {message}")


def main(argv):
    dry_run = "--dry-run" in argv
    argv = [a for a in argv if a != "--dry-run"]

    if len(argv) < 2:
        print("Usage: python3 -m WIEC_Component.hv_module_test <config file> "
              "[test name] [--dry-run]")
        return 2

    config_file = argv[1]
    test_name = argv[2] if len(argv) > 2 else (
        "dry_run" if dry_run else input("Input the test name:\n").strip()
    )

    if dry_run:
        session = _StandaloneSession(config_file, test_name)
        cfg = _cfg(session.json_data)
        # A dry run must never wait minutes for a crate that is not there
        session.json_data.update({
            "iseg_ramp_target_voltage": 100.0,
            "iseg_ramp_hold_seconds": 3.0,
            "iseg_ramp_sample_interval_s": 0.2,
            "iseg_ramp_rate": 50.0,
            # exercise the cross-check even without the divider wired up
            "iseg_ramp_daq_enabled": True,
            "iseg_ramp_daq_interval_s": 0.4,
        })
        mpod = SimulatedMPOD(slot=int(cfg["iseg_slot"]))
        console = AutoConsole(
            serial_number=mpod.serial_number,
            answers={"vmax_daq_channel": "20", "imax_daq_channel": "19"},
        )
        daq = SimulatedDAQ(mpod=mpod)
        return 0 if run(session, console=console, mpod=mpod, dry_run=True, daq=daq) else 1

    from .cli import run_single_item
    return run_single_item(sys.modules[__name__])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
