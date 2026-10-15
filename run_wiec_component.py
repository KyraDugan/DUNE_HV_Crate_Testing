""" run_wiec_component.py

Top script for the WIEC_Component test items.

Runs the DUNE WIEC test items one at a time, all of them writing into the
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

/home/dune-daq/Desktop/wiec-test.desktop   runs this from a shell on the desktop.

Hardware instruction slides (WIEC_utils.show_slides) appear at start-up (overview, finding the
serial, setting up the stand) and after a normal finish (taking the stand down).

-------------------------------------------------------------------------------------------------------
config.json keys used here (the item modules list their own):

"wiec_popups_enabled"                           Show the hardware instruction slides as pop-ups
"wiec_popup_dir"                                Folder of the exported slides (GUI/export_wiec_slides.sh)
"wiec_popup_pages"                              Step name -> slide number(s): overview, serial, setup, teardown
-------------------------------------------------------------------------------------------------------

"""

import argparse
import json
import os
import subprocess
import sys
import traceback

from WIEC_Component import preflight, WIEC_utils, record_results, output_log, wiec_report
from WIEC_Component import fan_test, fan_pwm_sweep, heater_test, hv_test, wiec_wib_qc
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
            status = WIEC_utils.verdict(results[item.KEY])
        else:
            status = "-"
        extra = "" if item in CRATE_ITEMS else "  (not part of the crate test)"
        print(f"  {number}) {item.NAME:<16} [{status:^4}] ~{item.EST_MINUTES:>2} min  "
              f"{item.DESCRIPTION}{extra}")


def run_item(session, item):
    WIEC_utils.print_banner(f"Running {item.NAME}", "item")
    # A new attempt replaces an earlier one that was stopped partway
    interrupted = session.datastore.setdefault("interrupted_items", [])
    if item.KEY in interrupted:
        interrupted.remove(item.KEY)

    try:
        passed = bool(item.run(session))
    except KeyboardInterrupt:
        print(f"\n{item.NAME} interrupted by the user")
        # Interrupted partway: shown as INTERRUPTED in the QC sheet (record_results.qc_sheet_row)
        session.datastore.setdefault("interrupted_items", []).append(item.KEY)
        session.emergency_shutoff()
        raise
    except BaseException:
        print(traceback.format_exc())
        print(f"{item.NAME} raised an exception -- powering down")
        session.datastore.setdefault("interrupted_items", []).append(item.KEY)
        session.emergency_shutoff()
        passed = False

    session.record(item.KEY, passed)
    print(f"\n{item.NAME}: {WIEC_utils.verdict(passed)}")
    return passed




def build_session(args, state):
    """Creates the session on first use, so --list and friends stay cheap."""
    if state.get("session") is None:

        # The serial slide comes before the serial prompt, before there is a session to read the config from
        with open(args.config) as f:
            config_data = json.load(f)
        popup = WIEC_utils.make_instruction_popup(config_data)
        WIEC_utils.show_slides(popup, config_data, "overview", "WIEC QC test")

        if args.wiec_serial:
            WIEC_serial = args.wiec_serial
        else:
            WIEC_utils.show_slides(popup, config_data, "serial", "Find the WIEC serial")
            WIEC_serial = WIEC_utils.confirm_input_ask("Enter WIEC serial:", "Reenter WIEC serial to confirm:")
        WIEC_serial_safe = record_results.file_name_safe(WIEC_serial)

        session = TestSession(args.config, WIEC_serial_safe)
        session.popup = popup       # the items show their hardware slides with it; may be None
        session.wiec_serial = WIEC_serial_safe
        session.datastore["wiec_serial"] = WIEC_serial_safe

        record_results.run_path(session)
        output_log.start(session)
        session.log(f"Results directory: {session.wiec_run_dir}")
        session.log(f"Legacy results directory: {session.results_path}")
        session.log(f"Spreadsheet row:   {session.row}")
        state["session"] = session

        username = WIEC_utils.confirm_input_ask("Enter your name:")
        session.datastore["username"] = username
        session.username = username

        WIEC_utils.show_slides(popup, config_data, "setup", "Set up the test stand")



    return state["session"]


def finish(session, beep=True):

    if session is None:
        return True
    
    config_data = session.json_data
    try:
        session.emergency_shutoff()
        passed = session.finalize()
        if beep:
            session.beep_sequence()
    finally:

        try:
            record_results.write_measurements(session)
        except Exception as error:
            session.log(f"Could not write measurements: {error}")

        try:
            record_results.write_qc_sheet(session, CRATE_ITEMS)
        except Exception as error:
            session.log(f"Could not write QC sheet: {error}")


        # Stored in run.json so a summary rebuilt later from the command line shows the item names
        item_names = {item.KEY: item.NAME for item in CRATE_ITEMS}
        session.datastore["item_names"] = item_names

        try:
            session.save_datastore(os.path.join(session.wiec_run_dir, config_data["wiec_run_json_name"]))
        except Exception as error:
            session.log(f"Could not write datastore: {error}")

        try:
            summary_path = wiec_report.write_report(session.wiec_run_dir, config_data, item_names)
            session.log(f"Summary: {summary_path}")
            # Only when there is a screen to show it on (not over SSH)
            if config_data["wiec_open_summary"] and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
                subprocess.Popen(["xdg-open", summary_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as error:
            session.log(f"Could not write summary: {error}")


        session.close_instruments()
        output_log.stop(session)

    # Teardown slides (unplug the HV cables, the PTC, ...) only after a normal finish: here
    # emergency_shutoff has powered everything down. After Ctrl-C or an error (beep=False)
    # the operator may want to retry rather than take the stand apart.
    if beep:
        WIEC_utils.show_slides(getattr(session, "popup", None), config_data, "teardown",
                               "Take down the test stand")

    return passed


def interactive(args, state, json_data):
    while True:
        session = state.get("session")
        results = session.results if session else {}
        print_items(results)
        print(WIEC_utils.color(WIEC_utils.rule(WIEC_utils.LIGHT_RULE), WIEC_utils.HEADER))
        if session:
            print(f" WIEC serial: {session.wiec_serial}")
            print(f"     results: {os.path.relpath(session.wiec_run_dir)}\n")
        # The letter to type for each option, in the colour used for operator actions.
        # The colour codes take no space on screen, so the columns stay lined up.
        def key(letter):
            return WIEC_utils.color(letter, WIEC_utils.ACTION)

        print(f"\n  {key('n')}) new user instructions         {key('q')}) save and quit            {key('f')}) run full test")
        print(  f"  {key('p')}) redo preflight                {key('s')}) shutdown instruments without quitting testing session\n")


        try:
            choice = WIEC_utils.ask("Select item number(s), or n/q/f/p/s: ").lower()
        except EOFError:
            print("\nInput closed -- finishing up")
            choice = "q"

        if choice == "n":
            preflight.print_suggestions(args.config, CRATE_ITEMS, json_data)
            continue

        if choice == "s":
            shutdown_only(args.config)
            continue

        if choice in ("q", "quit", "exit"):
            if session:
                print("Finishing the open session before quitting...")
                passed = finish(session)
                print(f"\nSession complete: {WIEC_utils.verdict(passed)}")
            return 0

        if choice == "p":
            preflight.print_report(preflight.check_environment(args.config))
            continue

        if choice == "f":
            session = build_session(args, state)
            for item in CRATE_ITEMS:
                if not run_item(session, item):
                    print(WIEC_utils.fail_text(f"\n{item.NAME} failed -- continuing with the next item."))
            session.log("Full test done: press q to save and quit.")
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
    parser.add_argument("--wiec_serial", help="wiec serial (asked for if not given)")
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
