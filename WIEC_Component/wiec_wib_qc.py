""" wiec_wib_qc.py

WIEC WIB QC Test Script

This is the top-level script for the wiec wib test.
This will work sequentially through the desired WIBs being tested.
The hardware will need to be moved between each WIB test, handled here.

runs under run_wiec_component.py umbrella

helper scripts:
- wib_setup.py          Initializes WIBs
- femb_checkout.py      Runs simplified FEMB checkout for WIEC 
- ptc_power.py          Runs PTC powering for the WIBs

-------------------------------------------------------------------------------------------------------
config.json keys used:

"wib_test_slots"                                Possible WIB test slots to be used in the setup (1-6, as on the
                                                flange; wib_setup converts to the PTC's 0-5)
"wib_boards_available"                          WIB boards that will be available to use for testing
"wiec_popup_pages"                              Slides for "wib_intro" (PTC, first WIB and FEMB cables in), "wib_move"
                                                (before every later batch) and "wib_teardown" (FEMB cables, WIB and
                                                PTC out at the end); see WIEC_utils.show_slides. Error slides
                                                "ptc_error", "wib_error" and "femb_error", shown when that check fails
"retry_check_phrase"                            Typed on an error slide; the failed check then runs once more
"wib_sensor_read_wait_seconds"                  Wait after the I2C scan before reading the WIB sensors

-------------------------------------------------------------------------------------------------------

"""

import json
import traceback
import subprocess
import os
import sys
import importlib

import wiec_serial as WIEC_SERIAL
from . import ptc_power
from . import wib_setup
from . import femb_checkout
from . import cli
from . import WIEC_utils
from . import record_results


NAME = "WIB/FEMB"
KEY = "wiec_wib_qc"
DESCRIPTION = "Sequential WIB powering and FEMB checkout to test Flange Board"
EST_MINUTES = 75



def prompt_wiec_test_info(session):
    """
    Asks the user which WIBs will be tested, confirms choice through reentry.
    Writes the confirmed WIB choice to session.WIBs_testing
    """
    config_data = session.json_data
    possible_slots = list(config_data.get("wib_test_slots"))
    if not possible_slots:
        raise ValueError("config.json is missing wib_test_slots")
    
    WIEC_utils.print_prompt_section("WIEC QC : Test Info")

    print("Testing all WIB slots.")
    planned = possible_slots
    session.WIBs_testing = planned
    return

    #The following code is good for debugging the QC process. It is not necessary for the test.
    #This code allows for the user to pick which WIB slots to be used instead of running all slots.
    while True:
        print("Which WIBs will you test?")
        print(f"  a) test all {possible_slots} WIB slots")
        print("  s) select which WIBs to be tested")
        choice = WIEC_utils.ask("Enter WIB choice:").lower()

        if choice in ("a", "all"):

            planned = possible_slots
            print(f"\nPlanned WIB test list: {planned}")
            while True:
                retry = WIEC_utils.ask('Type "a" or "all" to confirm all WIBs, or type "b" to go back: ').lower()

                if retry in ("a", "all"):
                    session.WIBs_testing = planned
                    return

                if retry == "b":
                    print("Returning to WIB selection menu.\n")
                    break

                print("Invalid input. Please type \"a\" or \"all\" to confirm, or \"b\" to go back.\n")

            continue

        if choice in ("s", "select"):
            while True:
                raw = WIEC_utils.ask(f"Enter WIBs to be tested separated by spaces (range {min(possible_slots)}-{max(possible_slots)}): "
                )

                try:
                    planned = [int(x) for x in raw.split()]
                except ValueError:
                    print("Invalid input. Please use space-separated integers.")
                    print('Example: "1 3 4"  (not "1,3,4")\n')
                    continue

                valid = set(possible_slots)
                if not set(planned).issubset(valid):
                    print(f"\nSelection invalid. Valid WIBs are {min(possible_slots)} through {max(possible_slots)}.")
                    print('Example: "1 3 4"  (not "1,3,4")')
                    continue

                planned = sorted(set(planned))

                while True:
                    retry = WIEC_utils.ask("Re-enter the WIB list to confirm, or type \"b\" to go back: ")

                    try:
                        repeated = [int(x) for x in retry.split()]
                    except ValueError:
                        if retry.strip().lower() == "b":
                            break
                        print("Invalid input. Please re-enter the exact WIB list as space-separated integers.")
                        print('Example: "1 3 4"  (not "1,3,4")\n')
                        continue

                    if retry.strip().lower() == "b":
                        break

                    if sorted(repeated) == sorted(planned):
                        session.WIBs_testing = planned
                        return

                    print("The list does not exactly match.")
                    print("Please re-enter the exact WIB list, or type \"b\" to go back.\n")

                break

        print("\nSelection invalid. Please choose a or s.")

def get_boards_available(session):
    """
    Make sure boards available to test with is a proper value
    """

    config_data = session.json_data
    boards = config_data.get("wib_boards_available")
    slots = len(config_data.get("wib_test_slots"))
    if not isinstance(boards, int) or not 1 <= boards <= slots:
        raise ValueError(f"wib_boards_available is {boards!r}. It must be between 1 and {slots}.")
    return boards


def make_batches(session):
    """
    Splits the desired slots to be tested into batches based on the WIBs available to test with.
    """

    slots = sorted(int(slot) for slot in session.WIBs_testing)
    boards = get_boards_available(session)
    return [slots[start:start + boards] for start in range(0, len(slots), boards)]


def start_ptc(session):
    """
    Fans, PTC power and Zynq login. If the PTC draws no current or the Zynq login fails, the PTC
    is powered down and the operator gets the PTC error slide; once they confirm, it is tried
    once more. A fan interlock failure stops straight away (the slide is about the PTC, not fans).
    Returns the Zynq serial connection, or None if the PTC failed.
    """
    for attempt in (1, 2):
        if not ptc_power.ensure_fans_on(session):
            return None
        if not ptc_power.turn_on_ptc(session):
            problem = "the PTC drew no current from the PL506"
        else:
            ser = wib_setup.login_zynq(session)
            if ser is not None:
                return ser
            problem = "the Zynq serial login failed"

        ptc_power.power_down(session)
        if attempt == 1:
            session.log(WIEC_utils.fail_text(f"PTC startup failed: {problem}. The PTC is powered off."))
            WIEC_utils.ask_to_fix_on_slide(getattr(session, "popup", None), session.json_data, "ptc_error",
                                           "PTC startup failed", [])

    session.log(WIEC_utils.fail_text(f"PTC startup failed again ({problem}). The WIB/FEMB test cannot run."))
    record_results.measure(session, ptc_power.KEY, "PTC startup", "failed", passed=False,
                           note=f"{problem} (after one retry)")
    return None


def power_on_slots(session, ser, slots):
    """
    Powers on the WIBs in slots, scans I2C and reads each WIB's sensors.
    Returns the sensor outputs ({slot: ...}) that wib_setup.check_slot reads.
    """
    power_map = {slot: "on" for slot in slots}
    wib_setup.power_wibs(ser, power_map)
    WIEC_SERIAL.run_ecat_soft_timeout_then_i2cdetect(ser)
    WIEC_utils.countdown_sleep(session, "Waiting before WIB sensor readout",
                               session.json_data.get("wib_sensor_read_wait_seconds"))
    return wib_setup.read_wib_sensors(ser, power_map)


def check_wib(session, ser, wib, sensor_outputs):
    """
    WIB checkout of the WIB in slot wib (I2C address and ping). On a failure the WIB is powered
    off and the operator gets the WIB error slide; once they confirm, the WIB is powered on and
    checked once more. Only the last attempt is recorded. Returns (passed, reason).
    """
    passed, reason, checks = wib_setup.check_slot(session, wib, sensor_outputs)
    attempts = 1
    if not passed:
        session.log(WIEC_utils.fail_text(f"WIB {wib} checkout failed: {reason}. "
                                         "Powering the WIB off so it can be checked."))
        wib_setup.power_wibs(ser, {wib: "off"})
        WIEC_utils.ask_to_fix_on_slide(getattr(session, "popup", None), session.json_data, "wib_error",
                                       f"WIB checkout failed: WIB slot {wib}",
                                       [], heading=f"WIB slot {wib}")
        sensor_outputs.update(power_on_slots(session, ser, [wib]))
        passed, reason, checks = wib_setup.check_slot(session, wib, sensor_outputs)
        attempts = 2
        if not passed:
            session.log(WIEC_utils.fail_text(f"WIB {wib} failed the WIB checkout "
                                             "again. Recorded as FAIL; moving on."))
    wib_setup.record_slot_checks(session, wib, checks, attempts)
    return passed, reason


def check_fembs(session, wib, femb_ids):
    """
    FEMB checkout through the WIB in slot wib. On a failure (the FEMBs are off, the WIB stays
    powered) the operator gets the FEMB error slide; once they confirm, it runs once more.
    Only the last attempt is recorded. Returns (passed, reason).
    """
    passed, reason = femb_checkout.run(session, wib, femb_ids, start_wib=True)
    wib_started = session.datastore["femb_checkout"][wib].get("wib_started")
    femb_checkout.print_femb_results(session, wib)
    attempts = 1
    if not passed:
        session.log(WIEC_utils.fail_text(f"WIB {wib} failed FEMB checkout: {reason}"))
        session.log("The FEMBs are switched off, the WIB is still powered.")
        lines = []
        # A swapped or missing data cable is the likely cause of the other failures, so say so
        mapping = femb_checkout.cable_mapping_problems(session.datastore["femb_checkout"][wib])
        if mapping:
            lines.append(f"Cable mapping wrong: {', '.join(mapping)}.")
        WIEC_utils.ask_to_fix_on_slide(getattr(session, "popup", None), session.json_data, "femb_error",
                                       f"FEMB checkout failed: WIB slot {wib}", lines,
                                       heading=f"WIB slot {wib}")
        # The retry replaces this record, so keep what the summary needs to say about attempt 1
        first_attempt = femb_checkout.first_attempt_summary(session, wib, session.datastore["femb_checkout"][wib])
        # wib_startup.py only runs again if it did not succeed the first time
        passed, reason = femb_checkout.run(session, wib, femb_ids, start_wib=not wib_started, retry=True)
        session.datastore["femb_checkout"][wib]["first_attempt"] = first_attempt
        femb_checkout.print_femb_results(session, wib)
        attempts = 2
        if not passed:
            session.log(WIEC_utils.fail_text(f"WIB {wib} failed FEMB checkout "
                                             "again. Recorded as FAIL; moving on."))
    femb_checkout.record_measurements(session, wib, attempts=attempts)
    return passed, reason


def run_batch(session, ser, batch, femb_sets, placed):
    """
    Runs the passthrough testing on the selected batch of WIBs, including the FEMB Checkout
    placed: the operator already confirmed on a slide that the WIBs and FEMB cables are in
    place; if not, the terminal prompt holds the test until they are
    """

    #Separates if only one WIB tested for grammar reasons in prompting the user
    if len(batch) == 1:
        WIEC_utils.print_prompt_section(f"Testing WIB {batch[0]}")
        session.log(f"Preparing test for WIB {batch[0]}.")
        instruction = f"Move WIB to slot {batch[0]}, then press Enter."
    else:
        WIEC_utils.print_prompt_section(f"Testing WIBs {batch}")
        session.log(f"Preparing test for WIBs {batch}")
        instruction = f"Move WIBs to slots {batch}, then press Enter."

    if placed:
        session.log(f"Operator confirmed the WIB placement for slot(s) {batch} on the pop-up.")
    else:
        WIEC_utils.ask(instruction)


    batch_results = []

    try:
        sensor_outputs = power_on_slots(session, ser, batch)


        for wib in batch:

            #WIB checkout: I2C address and ping, retried once after the WIB error slide
            passed, reason = check_wib(session, ser, wib, sensor_outputs)
            if not passed:
                batch_results.append([wib, passed, reason])
                continue


            #FEMB checkout, using FEMB set assigned to slot

            set_number, femb_ids, move_from = femb_checkout.femb_set_for_slot(femb_sets, batch, wib)
            if move_from is not None:
                WIEC_utils.ask(f"Move FEMB set {set_number} (FEMBs and cables) from WIB slot {move_from} "
                      f"to WIB slot {wib}, then press Enter.")
            session.log(f"WIB {wib}: using FEMB set {set_number} with FEMB IDs {femb_ids} for checkout.")

            # FEMB checkout, retried once after the FEMB error slide; only the last attempt is recorded
            passed, reason = check_fembs(session, wib, femb_ids)
            if not passed:
                batch_results.append([wib, False, reason])
                continue

            session.log(WIEC_utils.pass_text(f"WIB {wib} passed."))
            batch_results.append([wib, True, "WIB passed"])


    finally:
        wib_setup.power_wibs(ser, {slot: "off" for slot in batch})

    return batch_results


def run(session):
    json_data = session.json_data
    prompt_wiec_test_info(session)
    batches = make_batches(session)
    session.log(f"Testing in batches: {batches}")

    femb_sets = femb_checkout.get_femb_sets(session)
    femb_checkout.get_femb_tests(session)

    # Install the PTC, the first WIB and its FEMB cables while nothing is powered yet
    popup = getattr(session, "popup", None)     # None in a standalone run: terminal prompts only
    intro_shown = WIEC_utils.show_slides(popup, json_data, "wib_intro", "WIB/FEMB test set-up")

    passed = test_batches(session, batches, femb_sets, intro_shown)

    # test_batches powered the WIBs and the PTC down, so the FEMB cables, the WIB and the PTC
    # can come out. Ctrl-C or an error never gets here, so the operator can retry first.
    WIEC_utils.show_slides(popup, json_data, "wib_teardown", "Take out the WIB/FEMB test parts")
    return passed


def test_batches(session, batches, femb_sets, intro_shown):
    """
    Powers the PTC, then tests the batches one after another. intro_shown: the intro slides
    (which end with the first WIB and its FEMB cables in place) were shown. Before every later
    batch the move slides ask for the WIB and FEMB cables to go to the next slot.
    """
    json_data = session.json_data
    popup = getattr(session, "popup", None)

    WIEC_utils.print_prompt_section("PTC Startup")

    WIB_results = []
    ser = None
    try:
        # PTC power and Zynq login, retried once after the PTC error slide
        ser = start_ptc(session)
        if ser is None:
            return False
        for number, b in enumerate(batches):
            if number == 0:
                placed = intro_shown
            else:
                placed = WIEC_utils.show_slides(popup, json_data, "wib_move",
                                                "Move the WIB and FEMB cables to the next slot")
            WIB_results.extend(run_batch(session, ser, b, femb_sets, placed))
    finally:
        if ser is not None:
            WIEC_SERIAL.close_serial(ser)
        ptc_power.power_down(session)
        session.datastore["wiec_wib_qc_results"] = WIB_results

    return bool(WIB_results) and all(passed for _, passed, _ in WIB_results)

    
if __name__ == "__main__":
    sys.exit(cli.run_single_item(sys.modules[__name__]))



