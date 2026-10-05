"""Guided adjustment of the iseg module's Vmax and Imax limit potentiometers.

The tester turns a potentiometer with a screwdriver while the 34970A watches
the control voltage it produces.  This module does the watching: it reads the
selected DAQ channel twice a second and colours the reading green once it is
inside the acceptance window, so the tester can stop turning at the right
moment instead of squinting at a meter.

    Vmax   target 1.5625 V   accept 1.562  - 1.563   V  ( = 62.5 % = 1.25 kV )
    Imax   target 0.15625 V  accept 0.156  - 0.1565  V  ( = 6.25 % = 0.25 mA )

Those control voltages are a linear fraction of the module's full scale:
2.5 V is 100 %, so 1.5625 V is 62.5 % of 2 kV and 0.15625 V is 6.25 % of 4 mA.
The MPOD reports the resulting percentage over SNMP, which is what
hv_module_test cross-checks the adjustment against afterwards.

Used two ways:

    python3 demo.py                                    the bench tool
    from WIEC_Component.hv_limit_adjust import ...     inside hv_module_test

The pop-up windows the test procedure refers to are not written yet.  Every
tester notification goes through notify_tester(), which currently prints a
box -- give it a `popup` callable to add the real window without touching
anything else here.
"""

import select
import sys
import time
from dataclasses import dataclass

from WIEC_Component import WIEC_utils

# The 34970A on GPIB, the same instrument iseg_HV_electrical_test.py uses.
# NOT the LAN DAQ970A in config.json -- that is a different box.
GPIB_BOARD = 0
GPIB_ADDRESS = 9

# Slot 1 of the 34970A carries channels 101-120
SLOT_OFFSET = 100
MIN_CHANNEL = 11
MAX_CHANNEL = 20

# Which input channel each potentiometer is wired to, confirmed by the tester
# 2026-08-21.  Note the order: 19 is the CURRENT limit and 20 is the VOLTAGE
# limit, the opposite of the way the pair usually gets written down.
# 111-118 are the eight HV channel outputs, which is why only these two were
# free.  Every stage takes its own channel -- one channel used for both is
# what made a run measure the Imax pot and call it Vmax.
DEFAULT_CHANNELS = {"vmax": 20, "imax": 19}

SAMPLE_INTERVAL_S = 0.5
# Nobody adjusts a potentiometer for ten minutes.  A loop with no upper bound
# is a hang waiting to happen -- a simulated DAQ that never reached its target
# span it for as long as it was allowed to.
MAX_STAGE_SECONDS = 600.0
# Readings inside the window in a row before the value is called settled.  The
# voltage sweeps through the window while the pot is being turned, so a single
# lucky sample must not end the stage.
STABLE_SAMPLES = 3


# ----------------------------------------------------------------------
# Colour
# ----------------------------------------------------------------------
class Colour:
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    @classmethod
    def enabled(cls):
        return sys.stdout.isatty()

    @classmethod
    def paint(cls, text, colour, bold=False):
        if not cls.enabled():
            return text
        prefix = (cls.BOLD if bold else "") + colour
        return f"{prefix}{text}{cls.RESET}"

    @classmethod
    def green(cls, text, bold=False):
        return cls.paint(text, cls.GREEN, bold)

    @classmethod
    def yellow(cls, text, bold=False):
        return cls.paint(text, cls.YELLOW, bold)


# ----------------------------------------------------------------------
# The two stages
# ----------------------------------------------------------------------
@dataclass
class LimitStage:
    key: str
    label: str
    target_v: float
    low_v: float
    high_v: float
    what: str            # what the tester is turning
    percent: float       # what the target means as a fraction of full scale
    means: str           # ... in engineering units

    def in_window(self, value):
        return self.low_v <= value <= self.high_v

    def window_text(self):
        return f"{self.low_v:g} - {self.high_v:g} V"


VMAX_STAGE = LimitStage(
    key="vmax", label="Vmax",
    target_v=1.5625, low_v=1.562, high_v=1.563,
    what="the Vmax limit potentiometer",
    percent=62.5, means="1.25 kV of a 2 kV module",
)

IMAX_STAGE = LimitStage(
    key="imax", label="Imax",
    target_v=0.15625, low_v=0.156, high_v=0.1565,
    what="the Imax limit potentiometer",
    percent=6.25, means="0.25 mA of a 4 mA module",
)

STAGES = (VMAX_STAGE, IMAX_STAGE)


# ----------------------------------------------------------------------
# The 34970A
# ----------------------------------------------------------------------
class DAQ34970A:
    """Minimal GPIB reader for the 34970A.

    linux-gpib lives in /usr/local/lib/python3.10/dist-packages, so run with
    the system python.  Note the handle is closed once, at the end: closing it
    inside a read loop is what used to make every reading after the first fail
    with "libgpib: invalid descriptor".
    """

    def __init__(self, board=GPIB_BOARD, address=GPIB_ADDRESS):
        import gpib

        self._gpib = gpib
        self.dev = gpib.dev(board, address)
        gpib.timeout(self.dev, gpib.T10s)

    def idn(self):
        self._gpib.write(self.dev, "*IDN?")
        return self._gpib.read(self.dev, 256).decode().strip()

    def read_voltage(self, channel):
        self._gpib.write(self.dev, "MEAS:VOLT:DC? (@%d)" % channel)
        return float(self._gpib.read(self.dev, 256).decode().strip())

    def read_scan(self, first, last):
        """Reads a contiguous block in one command.

        Measured on this instrument: 1.70 s for eight channels as a scan
        against 2.22 s reading them one at a time.  Both are far slower than
        the 0.09 s SNMP sample, which is why the DAQ is sampled at its own
        slower interval rather than every time round the ramp loop.
        """
        self._gpib.write(self.dev, "MEAS:VOLT:DC? (@%d:%d)" % (first, last))
        raw = self._gpib.read(self.dev, 4096).decode().strip()
        return [float(v) for v in raw.split(",") if v.strip()]

    def close(self):
        try:
            self._gpib.close(self.dev)
        except Exception:
            pass


def prompt_channel(default=None, label=""):
    """Asks which slot-1 channel is wired to one potentiometer output.

    The label is not decoration: the two pots are on different channels, and
    a prompt that does not say which one it is asking about invites the
    answer for the other one.
    """
    suffix = f" [{default - SLOT_OFFSET}]" if default else ""
    what = f"{label} " if label else ""
    while True:
        answer = WIEC_utils.ask(
            f"    {what}DAQ channel ({MIN_CHANNEL}-{MAX_CHANNEL}){suffix}, "
            "q to stop: "
        ).strip().lower()
        if answer in ("q", "quit", "stop", "abort"):
            return None
        if not answer and default:
            return default
        try:
            channel = int(answer)
        except ValueError:
            print("    Not a number.")
            continue
        if not (MIN_CHANNEL <= channel <= MAX_CHANNEL):
            print(f"    Must be between {MIN_CHANNEL} and {MAX_CHANNEL}.")
            continue
        return channel + SLOT_OFFSET


# ----------------------------------------------------------------------
# Telling the tester what is about to happen
# ----------------------------------------------------------------------
def notify_tester(stage, channel, popup=None):
    """The '[see pop windows]' notification.

    The real window is not written yet, so this prints the box.  Pass a
    `popup(stage, channel)` callable to show a window as well -- nothing else
    in this file has to change when it arrives.
    """
    width = 68
    print()
    print("+" + "-" * (width - 2) + "+")
    print("|" + f"NOTIFICATION -- {stage.label} CHANGE WILL BEGIN".center(width - 2) + "|")
    print("+" + "-" * (width - 2) + "+")
    for line in (
        f"Adjust {stage.what} on the module.",
        "",
        f"Target readout : {stage.target_v:g} V",
        f"Accept window  : {stage.window_text()}",
        f"Which means    : {stage.percent:g} % of full scale = {stage.means}",
        f"Watching       : DAQ channel {channel}",
        "",
        "See the pop-up window for the procedure.",
        "(the pop-up windows will be updated next week)",
    ):
        print("| " + line.ljust(width - 4) + " |")
    print("+" + "-" * (width - 2) + "+")

    if popup is not None:
        try:
            popup(stage, channel)
        except Exception as exc:              # a broken pop-up must not stop the test
            print(f"    (pop-up failed: {exc})")


# ----------------------------------------------------------------------
# The monitor loop
# ----------------------------------------------------------------------
def _key_pressed():
    """Whatever the tester typed, without blocking the 0.5 s sampling."""
    if not sys.stdin.isatty():
        return None
    ready, _, _ = select.select([sys.stdin], [], [], 0)
    if not ready:
        return None
    return sys.stdin.readline().strip().lower()


def monitor_stage(read_value, stage, channel, interval=SAMPLE_INTERVAL_S,
                  stable_samples=STABLE_SAMPLES, popup=None, auto_accept=None,
                  timeout_s=MAX_STAGE_SECONDS):
    """Prints the channel twice a second while the tester turns the pot.

    Green once the reading is inside the window, yellow while it is not, and
    an up/down hint so the tester knows which way to turn.  Returns
    (accepted, summary).

    auto_accept ends the stage by itself once the reading has settled, instead
    of waiting for Enter.  It defaults to on when stdin is not a terminal --
    without it a --dry-run, or anything driven from a pipe, would loop for
    ever waiting for a keypress that cannot arrive.
    """
    if auto_accept is None:
        auto_accept = not sys.stdin.isatty()
    notify_tester(stage, channel, popup=popup)

    print()
    print(f"    Turn {stage.what} until the reading goes green.")
    print("    Press Enter to accept the current value, q to stop.")
    print()

    in_window_run = 0
    settled = False
    last_value = float("nan")
    samples = 0
    started_at = time.monotonic()

    while True:
        try:
            value = read_value(channel)
        except Exception as exc:
            print(f"    read failed: {exc}")
            time.sleep(interval)
            continue

        samples += 1
        last_value = value
        inside = stage.in_window(value)
        in_window_run = in_window_run + 1 if inside else 0
        settled = in_window_run >= stable_samples

        difference = value - stage.target_v
        if inside:
            hint = "IN RANGE"
        elif difference < 0:
            hint = "turn UP  "
        else:
            hint = "turn DOWN"

        line = (
            f"  {time.strftime('%H:%M:%S')}  {stage.label}  {value:10.6f} V"
            f"   target {stage.target_v:g} V   diff {difference:+9.6f} V   {hint}"
        )
        print(Colour.green(line, bold=settled) if inside else Colour.yellow(line))

        if settled:
            print("    " + Colour.green(
                f"{stage.label} is good now"
                + ("" if auto_accept else " -- press Enter to accept"), bold=True))

        answer = _key_pressed()
        if answer is None and auto_accept and settled:
            answer = ""            # settled and nobody to press Enter: accept it

        if answer is None and time.monotonic() - started_at > timeout_s:
            print("    " + Colour.yellow(
                f"{stage.label} adjustment gave up after {timeout_s:.0f} s", bold=True))
            return False, {
                "stage": stage.key, "channel": channel, "value_v": value,
                "target_v": stage.target_v, "window": stage.window_text(),
                "in_window": inside, "settled": settled, "samples": samples,
                "result": f"timed out after {timeout_s:.0f} s",
            }
        if answer is not None:
            if answer in ("q", "quit", "stop", "abort"):
                return False, {
                    "stage": stage.key, "channel": channel, "value_v": value,
                    "in_window": inside, "settled": settled, "samples": samples,
                    "result": "stopped by the tester",
                }
            # Enter, or anything else, accepts what is on screen now
            elapsed = time.monotonic() - started_at
            summary = {
                "stage": stage.key, "channel": channel, "value_v": value,
                "target_v": stage.target_v, "window": stage.window_text(),
                "in_window": inside, "settled": settled,
                "samples": samples, "seconds": round(elapsed, 1),
                "result": "good" if inside else "accepted OUTSIDE the window",
            }
            if inside:
                print("    " + Colour.green(
                    f"{stage.label} is good now: {value:.6f} V "
                    f"({stage.window_text()})", bold=True))
            else:
                print("    " + Colour.yellow(
                    f"{stage.label} accepted at {value:.6f} V, which is OUTSIDE "
                    f"{stage.window_text()}", bold=True))
            return inside, summary

        time.sleep(interval)


def resolve_channel(stage, channels=None, ask=True):
    """The channel for one stage: caller's, else the default, else ask."""
    channels = channels or {}
    channel = channels.get(stage.key)
    if channel is not None:
        return channel if channel > SLOT_OFFSET else channel + SLOT_OFFSET

    default = DEFAULT_CHANNELS.get(stage.key)
    default_full = default + SLOT_OFFSET if default else None
    if not ask:
        return default_full
    return prompt_channel(default_full, label=stage.label)


def run_adjustment(read_value, channels=None, stages=STAGES, popup=None, ask=True):
    """Walks the Vmax then Imax stages.

    `channels` maps a stage key to its input channel; anything missing is
    asked for, per stage.  There is deliberately no single-channel form --
    that is what let one run watch the Imax pot through the whole Vmax stage
    and report "Vmax is good now".
    """
    results = {}
    all_good = True

    for index, stage in enumerate(stages, start=1):
        print()
        print("=" * 70)
        print(f" STEP {index} of {len(stages)} -- {stage.label} limit adjustment")
        print("=" * 70)

        channel = resolve_channel(stage, channels, ask=ask)
        if channel is None:
            print(f"    No DAQ channel for {stage.label}, stopping.")
            return False, results

        good, summary = monitor_stage(read_value, stage, channel, popup=popup)
        results[stage.key] = summary
        all_good = all_good and good

        if summary["result"] == "stopped by the tester":
            print(f"\n    {stage.label} stage stopped by the tester.")
            return False, results

        if index < len(stages):
            print("\n    Next step.")

    return all_good, results


# ----------------------------------------------------------------------
# Standalone
# ----------------------------------------------------------------------
def main(channels=None):
    print("=" * 70)
    print(" HV MODULE LIMIT ADJUSTMENT -- Vmax and Imax")
    print("=" * 70)
    print(" Each potentiometer has its own DAQ channel:")
    for stage in STAGES:
        print(f"   {stage.label:<5} default input channel "
              f"{DEFAULT_CHANNELS.get(stage.key)}")
    print("=" * 70)

    try:
        daq = DAQ34970A()
    except Exception as exc:
        print(f"Could not open the 34970A on GPIB {GPIB_BOARD}:{GPIB_ADDRESS}: {exc}")
        print("Run with the system python: /usr/bin/python3")
        return 1

    try:
        print("Instrument:", daq.idn())
        all_good, results = run_adjustment(daq.read_voltage, channels)

        print()
        print("=" * 70)
        for stage in STAGES:
            summary = results.get(stage.key)
            if not summary:
                print(f"  {stage.label:<6} not done")
                continue
            text = (f"  {stage.label:<6} ch {summary['channel']}  "
                    f"{summary['value_v']:10.6f} V   {summary['result']}")
            print(Colour.green(text) if summary.get("in_window") else Colour.yellow(text))
        print("=" * 70)
        print(" RESULT:", "PASS" if all_good else "FAIL")
        return 0 if all_good else 1
    except (EOFError, KeyboardInterrupt):
        print("\nStopped.")
        return 130
    finally:
        daq.close()


if __name__ == "__main__":
    sys.exit(main())
