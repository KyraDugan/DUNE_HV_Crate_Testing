#!/usr/bin/env python3
"""Bench tool for the HV module limit potentiometers, on the 34970A over GPIB.

    /usr/bin/python3 demo.py            adjust Vmax then Imax  (default)
    /usr/bin/python3 demo.py --read     the old interactive channel readout
    /usr/bin/python3 demo.py --vmax-channel 20 --imax-channel 19

Run with the SYSTEM python -- the linux-gpib bindings live in
/usr/local/lib/python3.10/dist-packages and are NOT installed in this
project's venv.

The tester turns a potentiometer while this prints the control voltage twice a
second, green once it is inside the acceptance window:

    Vmax   input channel 20   target 1.5625 V   accept 1.562 - 1.563   V
    Imax   input channel 19   target 0.15625 V  accept 0.156 - 0.1565  V

Each pot has its own channel and is asked for separately.  Using one channel
for both stages made a run watch the Imax pot for the whole Vmax stage and
report "Vmax is good now", back when the Vmax window was wide enough to let
1.551 V through.

The work itself lives in WIEC_Component/hv_limit_adjust.py, so this bench tool
and the hv_module_test item share one implementation instead of two copies
that drift apart.  Everything here is the command line and the old readout.
"""

import argparse
import os
import sys

# The package sits next to this script; work whichever directory we started in
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from WIEC_Component.hv_limit_adjust import (  # noqa: E402
    DAQ34970A, DEFAULT_CHANNELS, MAX_CHANNEL, MIN_CHANNEL,
    main as adjust_main, prompt_channel,
)


def read_loop(daq):
    """The original behaviour: ask for a channel, print one reading, repeat."""
    while True:
        channel = prompt_channel()
        if channel is None:
            break
        try:
            print("Channel %d = %.6f V" % (channel, daq.read_voltage(channel)))
        except Exception as exc:
            print("read failed:", exc)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--read", action="store_true",
                        help="just read channels, no limit adjustment")
    parser.add_argument("--vmax-channel", type=int, default=None,
                        help=f"DAQ channel {MIN_CHANNEL}-{MAX_CHANNEL} on the Vmax "
                             f"pot (default {DEFAULT_CHANNELS['vmax']})")
    parser.add_argument("--imax-channel", type=int, default=None,
                        help=f"DAQ channel {MIN_CHANNEL}-{MAX_CHANNEL} on the Imax "
                             f"pot (default {DEFAULT_CHANNELS['imax']})")
    args = parser.parse_args()

    channels = {}
    for key, value in (("vmax", args.vmax_channel), ("imax", args.imax_channel)):
        if value is None:
            continue
        if not (MIN_CHANNEL <= value <= MAX_CHANNEL):
            parser.error(f"--{key}-channel must be between {MIN_CHANNEL} and {MAX_CHANNEL}")
        channels[key] = value

    if not args.read:
        return adjust_main(channels)

    try:
        daq = DAQ34970A()
    except Exception as exc:
        print("Could not open the 34970A:", exc)
        return 1

    try:
        print("Instrument:", daq.idn())
        read_loop(daq)
    except (EOFError, KeyboardInterrupt):
        print()
    finally:
        # Closing belongs here, NOT inside the loop -- closing it after the
        # first reading is what made every following read fail with
        # "libgpib: invalid descriptor"
        daq.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
