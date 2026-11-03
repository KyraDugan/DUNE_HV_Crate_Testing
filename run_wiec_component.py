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

Hardware instruction slides (WIEC_utils.show_slides, from WIEC_Component/WIEC_Popups.pdf) appear
at start-up (overview, serial, the visual inspection steps, setting up the stand, the SHV check and
the HV warning) and after a normal finish (taking the stand down, the QC pass or fail slide).
wiec_wib_qc shows its own: putting in the PTC/WIB/FEMB cables, moving them, taking them out.

-------------------------------------------------------------------------------------------------------
config.json keys used here (the item modules list their own):

"wiec_popups_enabled"                           Show the hardware instruction slides as pop-ups
"wiec_popup_dir"                                Folder of the exported slides (GUI/export_wiec_slides.sh)
"wiec_popup_pages"                              Step name -> slide number(s): overview, serial, visual_inspection (one
                                                per step), setup, shv_check, hv_warning, teardown, qc_pass, qc_fail
"vi_steps"                                      Name of each visual inspection step, one per visual_inspection slide
"vi_pass_phrase"                                Pass button / what the operator types when a step passed
"vi_fail_phrase"                                Fail button / what they type when it failed (no tests run, QC fails)
"shv_check_phrase"                              What the operator types after checking the SHV cable mapping
"hv_warning_phrase"                             What the operator types to acknowledge the HV warning (see hv_test)
"wiec_run_json_name"                            Name of the run JSON written into each run folder
"wiec_open_summary"                             Open the HTML summary in a browser window when the run ends
-------------------------------------------------------------------------------------------------------

"""

import argparse
import json
import os
import sys
import traceback
import time

from WIEC_Component import preflight, WIEC_utils, record_results, output_log, wiec_report
from WIEC_Component import fan_test, fan_pwm_sweep, heater_test, hv_test, wiec_wib_qc
from WIEC_Component.session import TestSession, shutdown_only



# The crate test items, in the order they should normally run
CRATE_ITEMS = [fan_test, fan_pwm_sweep, heater_test, hv_test, wiec_wib_qc]
ALL_ITEMS = CRATE_ITEMS

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
    """
    Runs one item, recording the result in the session.
    Returns True if it passed, False if it failed or raised an exception.
    """

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


def ask_serial(popup, config_data):
    """
    Asks for the WIEC serial twice on the serial slide; in the terminal if the slide has no window.
    """
    message = ""
    while True:
        answer = WIEC_utils.ask_on_slide(popup, config_data, "serial", "Find the WIEC serial",
                                         "Enter the WIEC serial, then enter it again to confirm.",
                                         fields=("WIEC serial", "Re-enter WIEC serial"),
                                         message=message)
        if answer is None:
            return WIEC_utils.confirm_input_ask("Enter WIEC serial:", "Reenter WIEC serial to confirm:")
        first, second = answer["fields"]
        if not first:
            message = "You must enter a serial."
        elif first != second:
            message = "The two serials do not match. Please try again."
        else:
            return first


def ask_visual_inspection(popup, config_data):
    """
    Goes through the visual inspection steps (config "vi_steps", one slide each), each with a
    pass and a fail button. Stops at the first failed step: no tests run after a failed
    inspection. Returns [(step, result, reason), ...] for the steps checked, the result "PASS"
    or "FAIL" and the reason "" on a pass.
    """
    results = []
    steps = config_data["vi_steps"]
    for index, step in enumerate(steps):
        title = f"Visual inspection {index + 1}/{len(steps)}: {step}"
        result, reason = ask_vi_step(popup, config_data, index, title)
        results.append((step, result, reason))
        if result == "FAIL":
            break
    return results


def ask_vi_step(popup, config_data, index, title):
    """
    Asks whether one visual inspection step passed, on its slide (pass / fail buttons). A fail
    opens the slide again to confirm it, with a box for the reason and a way back in case fail
    was a mis-click. In the terminal if the slide has no window. Returns (result, reason).
    """
    pass_phrase = config_data["vi_pass_phrase"]
    fail_phrase = config_data["vi_fail_phrase"]

    while True:
        answer = WIEC_utils.ask_on_slide(popup, config_data, "visual_inspection", title,
                                         "Did this check pass?",
                                         buttons=(pass_phrase, fail_phrase), index=index)
        if answer is None:
            break       # no window: the terminal questions below
        if answer["button"] == pass_phrase:
            return "PASS", ""

        # Failed: confirm, since a fail ends the whole test. "Back" returns to pass / fail
        message = ""
        while True:
            answer = WIEC_utils.ask_on_slide(popup, config_data, "visual_inspection", title,
                                             "A failed visual inspection ends the test. No tests will be run if confirmed.\n"
                                             "Describe why it failed, then confirm.",
                                             fields=("Reason",), buttons=("Confirm fail", "Back"),
                                             message=message, index=index)
            if answer is None or answer["button"] == "Back":
                break
            if answer["fields"][0]:
                return "FAIL", answer["fields"][0]
            message = "A reason is needed for a failed visual inspection."
        if answer is None:
            # Reason window closed: the fail was chosen, so confirm it and ask the reason in the terminal
            if confirm_vi_fail(fail_phrase):
                return "FAIL", ask_vi_reason()

    print(WIEC_utils.color(title, WIEC_utils.HEADER))
    while True:
        VI_input = WIEC_utils.ask(f"If this check passed, type '{pass_phrase}'. If it failed, type '{fail_phrase}':")
        if WIEC_utils.same_phrase(VI_input, pass_phrase):
            return "PASS", ""
        if WIEC_utils.same_phrase(VI_input, fail_phrase):
            if confirm_vi_fail(fail_phrase):
                return "FAIL", ask_vi_reason()
            continue
        print(WIEC_utils.color(f"Please type '{pass_phrase}' or '{fail_phrase}' exactly.", WIEC_utils.FAIL_COLOR))


def confirm_vi_fail(fail_phrase):
    """
    Asks in the terminal to confirm a failed visual inspection step, since it ends the test.
    True if confirmed; anything else goes back to the pass / fail question.
    """
    answer = WIEC_utils.ask(f"A failed visual inspection ends the test: no tests will be run. "
                            f"Type '{fail_phrase}' again to confirm, or press Enter to go back:")
    return WIEC_utils.same_phrase(answer, fail_phrase)


def ask_vi_reason():
    """
    Asks in the terminal why the visual inspection failed, until a reason is given.
    """
    reason = WIEC_utils.ask("Describe why the visual inspection failed:")
    while not reason:
        print(WIEC_utils.color("A reason is needed for a failed visual inspection.", WIEC_utils.FAIL_COLOR))
        reason = WIEC_utils.ask("Describe why the visual inspection failed:")
    return reason


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
            WIEC_serial = ask_serial(popup, config_data)
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

        VI_steps = ask_visual_inspection(popup, config_data)
        VI_result = "FAIL" if VI_steps[-1][1] == "FAIL" else "PASS"
        session.datastore["VI_result"] = VI_result
        session.log(f"Visual inspection: {VI_result}")

        # A failed inspection needs the reason: it goes in the CSV note, the QC sheet's Why and the summary
        if VI_result == "FAIL":
            step, _, reason = VI_steps[-1]
            session.datastore["VI_reason"] = f"{step}: {reason}"
            session.log(f"Visual inspection failed at {step}: {reason}")

        # One row per step checked. No value: PASS/FAIL is the result itself
        for step, result, reason in VI_steps:
            record_results.measure(session, "visual_inspection", step, None,
                                   passed=(result == "PASS"), note=reason)

        # Set up the stand, check the SHV cable mapping and acknowledge the HV warning, all
        # before any test runs. Not after a failed inspection: nothing gets plugged in then.
        if VI_result == "PASS":
            WIEC_utils.show_slides(popup, config_data, "setup", "Set up the test stand", button="Continue")
            WIEC_utils.ask_phrase_on_slide(popup, config_data, "shv_check", "Check the SHV cables",
                                           "Check every SHV cable is on its labelled connector.",
                                           config_data["shv_check_phrase"], "Continue")
            session.log("Operator checked the SHV cable mapping.")
            hv_test.acknowledge_hv_warning(session)


    return state["session"]


def inspection_failed(session):
    """True if the operator failed the visual inspection: no tests run and the whole QC fails."""
    if session.datastore.get("VI_result") == "FAIL":
        print(WIEC_utils.fail_text("\nVisual inspection failed -- skipping all tests, the WIEC QC is a FAIL."))
        return True
    return False


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
                wiec_report.open_summary(summary_path, config_data)    # on the other monitor
        except Exception as error:
            session.log(f"Could not write summary: {error}")


        session.close_instruments()
        output_log.stop(session)

    # Teardown slides (unplug the HV cables, the PTC, ...) only after a normal finish: here
    # emergency_shutoff has powered everything down. After Ctrl-C or an error (beep=False)
    # the operator may want to retry rather than take the stand apart.
    if beep:
        popup = getattr(session, "popup", None)

        # A failed visual inspection ran no tests and plugged nothing in: no teardown, just the fail slide
        if session.datastore.get("VI_result") == "FAIL":
            WIEC_utils.show_slides(popup, config_data, "qc_fail", "WIEC QC failed")
            return passed

        # The WIB, FEMB cables and PTC came out at the end of wiec_wib_qc; this is the rest
        WIEC_utils.show_slides(popup, config_data, "teardown", "Take down the test stand")

        # The pass slide needs every crate item run and passed; one failed item gives the fail
        # slide. A run of only some items with no failures (e.g. --items fan_test) shows
        # neither, because the crate has not been fully tested.
        crate_results = [session.results.get(item.KEY) for item in CRATE_ITEMS]   # None = not run
        if all(crate_results):
            WIEC_utils.show_slides(popup, config_data, "qc_pass", "WIEC QC passed")
        elif False in crate_results:
            WIEC_utils.show_slides(popup, config_data, "qc_fail", "WIEC QC failed")

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
            if inspection_failed(session):
                finish(session)
                return 0
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
        if inspection_failed(session):
            finish(session)
            return 0
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
            if not inspection_failed(session):
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
