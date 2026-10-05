#!/usr/bin/env python3
"""Top script for the WIEC_Component test items.

Runs the DUNE HV crate test items one at a time, all of them writing into the
same spreadsheet row and results folder.

    python3 run_wiec_component.py                       menu
    python3 run_wiec_component.py --all                 every crate item
    python3 run_wiec_component.py --items               fan_test,heater_test
    python3 run_wiec_component.py --list                what is available
    python3 run_wiec_component.py --suggest             the first-run notes
    python3 run_wiec_component.py --preflight           checks only
    python3 run_wiec_component.py --shutdown            power everything down

Run it from the repository root: caen_r8033dm.py loads its .so from the
working directory.
"""

import argparse
import json
import os
import sys
import traceback

from WIEC_Component import WIEC_utils
from WIEC_Component import fan_test, fan_pwm_sweep, heater_test, hv_test, wiec_wib_qc
from WIEC_Component import preflight
from WIEC_Component.session import TestSession, shutdown_only



# The crate test items, in the order they should normally run
CRATE_ITEMS = [fan_test, fan_pwm_sweep, heater_test, hv_test, wiec_wib_qc]
#EXTRA_ITEMS = [wiec_qc]
ALL_ITEMS = CRATE_ITEMS #+ EXTRA_ITEMS

BANNER = "=" * 70


def item_by_key(key):
    for item in ALL_ITEMS:
        if item.KEY == key:
            return item
    return None


def print_items(results=None):
    results = results or {}
    WIEC_utils.print_banner("TEST ITEMS", "menu")
    for number, item in enumerate(ALL_ITEMS, start=1):
        if item.KEY in results:
            status = "PASS" if results[item.KEY] else "FAIL"
        else:
            status = "-"
        extra = "" if item in CRATE_ITEMS else "  (not part of the crate test)"
        print(f"  {number}) {item.NAME:<16} [{status:^4}] ~{item.EST_MINUTES:>2} min  "
              f"{item.DESCRIPTION}{extra}")


def run_item(session, item):
    WIEC_utils.print_banner(f"Running {item.NAME}", "item")
    try:
        passed = bool(item.run(session))
    except KeyboardInterrupt:
        print(f"\n{item.NAME} interrupted by the user")
        session.emergency_shutoff()
        raise
    except BaseException:
        print(traceback.format_exc())
        print(f"{item.NAME} raised an exception -- powering down")
        session.emergency_shutoff()
        passed = False

    session.record(item.KEY, passed)
    print(f"\n{item.NAME}: {'PASS' if passed else 'FAIL'}")
    return passed


def build_session(args, state):
    """Creates the session on first use, so --list and friends stay cheap."""
    if state.get("session") is None:
        test_name = args.name or WIEC_utils.ask("Input the test name:")
        session = TestSession(args.config, test_name)
        session.log(f"Results directory: {session.results_path}")
        session.log(f"Spreadsheet row:   {session.row}")
        state["session"] = session
    return state["session"]


def finish(session, beep=True):
    
    if session is None:
        return True

    try:
        session.emergency_shutoff()
        passed = session.finalize()
        if beep:
            session.beep_sequence()
    finally:
        session.close_instruments()
    return passed


def interactive(args, state, json_data):
    while True:
        session = state.get("session")
        results = session.results if session else {}
        print_items(results)
        if session:
            print(f" test   : {session.test_name}")
            print(f" results: {session.results_path}  (row {session.row})")
        print("\n  a) run all crate items      p) preflight        h) suggestions")
        print("  s) power everything down    f) finish and write the verdict")
        print("  q) quit\n")

        try:
            choice = WIEC_utils.ask("Select item number(s), or a/p/h/s/f/q: ").lower()
        except EOFError:
            print("\nInput closed -- finishing up")
            choice = "q"

        if choice in ("q", "quit", "exit"):
            if session:
                print("Finishing the open session before quitting...")
                finish(session)
            return 0

        if choice == "f":
            if session is None:
                print("Nothing has run yet.")
                continue
            passed = finish(session)
            state["session"] = None
            print(f"\nSession complete: {'PASS' if passed else 'FAIL'}")
            continue

        if choice == "p":
            preflight.print_report(preflight.check_environment(args.config))
            continue

        if choice == "h":
            preflight.print_suggestions(args.config, CRATE_ITEMS, json_data)
            continue

        if choice == "s":
            shutdown_only(args.config)
            continue

        if choice == "a":
            session = build_session(args, state)
            for item in CRATE_ITEMS:
                if not run_item(session, item):
                    print(f"\n{item.NAME} failed -- continuing with the next item.")
            continue

        selected = []
        for token in choice.replace(",", " ").split():
            if token.isdigit() and 1 <= int(token) <= len(ALL_ITEMS):
                selected.append(ALL_ITEMS[int(token) - 1])
            elif item_by_key(token):
                selected.append(item_by_key(token))
            else:
                print(f"Did not understand {token!r}")
                selected = []
                break

        if not selected:
            continue

        session = build_session(args, state)
        for item in selected:
            run_item(session, item)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.json", help="config file")
    parser.add_argument("--name", help="test name (asked for if not given)")
    parser.add_argument("--items", help="comma separated item keys to run")
    parser.add_argument("--all", action="store_true", help="run every crate item")
    parser.add_argument("--list", action="store_true", help="list the items and exit")
    parser.add_argument("--suggest", action="store_true", help="print the notes and exit")
    parser.add_argument("--preflight", action="store_true", help="run the checks and exit")
    parser.add_argument("--shutdown", action="store_true", help="power everything down")
    parser.add_argument("--no-ping", action="store_true", help="skip the ping checks")
    args = parser.parse_args()

    if args.list:
        print_items()
        return 0

    if args.shutdown:
        return 0 if shutdown_only(args.config) else 1

    json_data = None
    if os.path.isfile(args.config):
        with open(args.config) as f:
            json_data = json.load(f)

    if args.suggest:
        preflight.print_suggestions(args.config, CRATE_ITEMS, json_data)
        return 0

    first_run = preflight.is_first_run()

    WIEC_utils.print_banner("WIEC COMPONENT -- DUNE HV CRATE TEST ITEMS", "title")
    checks = preflight.check_environment(args.config, ping_instruments=not args.no_ping)
    environment_ok = preflight.print_report(checks)

    if first_run:
        preflight.print_suggestions(args.config, CRATE_ITEMS, json_data)

    if args.preflight:
        return 0 if environment_ok else 1

    if not environment_ok:
        print("\nPreflight found problems. Fix them, or continue anyway if you know")
        print("the failing check does not matter for the items you are running.")
        if WIEC_utils.ask("Continue? [y/N]: ").strip().lower() not in ("y", "yes"):
            return 1

    state = {"session": None}
    exit_code = 0

    try:
        if args.all or args.items:
            if args.items:
                items = []
                for key in args.items.split(","):
                    item = item_by_key(key.strip())
                    if item is None:
                        print(f"Unknown item {key!r}. Known: "
                              f"{', '.join(i.KEY for i in ALL_ITEMS)}")
                        return 2
                    items.append(item)
            else:
                items = CRATE_ITEMS

            session = build_session(args, state)
            for item in items:
                run_item(session, item)
            exit_code = 0 if finish(session) else 1
            state["session"] = None
        else:
            exit_code = interactive(args, state, json_data)
    except KeyboardInterrupt:
        print("\n\nInterrupted -- powering everything down")
        if state.get("session"):
            finish(state["session"], beep=False)
        else:
            shutdown_only(args.config)
        exit_code = 130
    except BaseException:
        print(traceback.format_exc())
        print("\nUnexpected error -- powering everything down")
        if state.get("session"):
            finish(state["session"], beep=False)
        exit_code = 1
    finally:
        preflight.mark_run(extra={"last_config": args.config})

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
