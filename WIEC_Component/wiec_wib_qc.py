""" WIEC WIB QC Test Script

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

"wib_test_slots"                                Possible WIB test slots to be used in the setup
"wib_boards_available"                          WIB boards that will be available to use for testing

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


NAME = "WIEC WIB QC test"
KEY = "wiec_wib_qc"
DESCRIPTION = "Sequential WIB powering and FEMB checkout to test Flange Board"
EST_MINUTES = 30



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

    while True:
        print("Which WIBs will you test?")
        print(f"  a) test all {possible_slots} WIB slots")
        print("  s) select which WIBs to be tested")
        choice = input().strip().lower()

        if choice in ("a", "all"):

            planned = possible_slots
            print(f"\nPlanned WIB test list: {planned}")
            while True:
                retry = input(
                    'Type "a" or "all" to confirm all WIBs, or type "b" to go back: '
                ).strip().lower()

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
                raw = input(f"Enter WIBs to be tested separated by spaces (range {min(possible_slots)}-{max(possible_slots)}): "
                ).strip()

                try:
                    planned = [int(x) for x in raw.split()]
                except ValueError:
                    print("Invalid input. Please use space-separated integers.")
                    print('Example: "0 2 3"  (not "0,2,3")\n')
                    continue

                valid = set(possible_slots)
                if not set(planned).issubset(valid):
                    print(f"\nSelection invalid. Valid WIBs are {min(possible_slots)} through {max(possible_slots)}.")
                    print('Example: "0 2 3"  (not "0,2,3")')
                    continue

                planned = sorted(set(planned))

                while True:
                    retry = input(
                        "\nRe-enter the WIB list to confirm, or type \"b\" to go back: "
                    ).strip()

                    try:
                        repeated = [int(x) for x in retry.split()]
                    except ValueError:
                        if retry.strip().lower() == "b":
                            break
                        print("Invalid input. Please re-enter the exact WIB list as space-separated integers.")
                        print('Example: "0 2 3"  (not "0,2,3")\n')
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


def run_batch(session, ser, batch):
    """
    Runs the passthrough testing on the selected batch of WIBs, including the FEMB Checkout
    Prompts the user to properly set up the hardware for next WIB slot
    """

    config_data = session.json_data

    #Separates if only one WIB tested for grammar reasons in prompting the user
    if len(batch) == 1:

        WIEC_utils.print_prompt_section(f"Testing WIB {batch[0]}")
        session.log(f"Preparing test for WIB {batch[0]}.")
        print(f"\nMove hardware to WIB {batch[0]} and press Enter.")
    else:

        WIEC_utils.print_prompt_section(f"Testing WIBs {batch}")
        session.log(f"Preparing test for WIBs {batch}")
        print(f"Move WIBs to slots {batch} and press Enter.")

    #PLACEHOLDER FOR BETTER INSTRUCTIONS!!!!!

    input()

    power_map = {slot: "on" for slot in batch}
    batch_results = []

    try: 
        WIEC_SERIAL.power_wib(ser, power_map)
        WIEC_SERIAL.run_ecat_soft_timeout_then_i2cdetect(ser)
        WIEC_utils.countdown_sleep(session, "Waiting before WIB sensor readout", config_data.get("wib_sensor_read_wait_seconds"))
        sensor_outputs = WIEC_SERIAL.sensors_addr(ser, power_map)


        for wib in batch:
            passed, reason = wib_setup.check_slot(session, wib, sensor_outputs)
            if not passed:
                batch_results.append([wib, passed, reason])
                continue

            #FEMB to be implemented
            
            #passed, reason = femb_checkout.run(session, wib)
           #if not passed:
               # batch_results.append([wib, passed, reason])
              #  continue

            session.log(f"WIB {wib} passed.")
            batch_results.append([wib, True, "WIB passed"])

    finally:
        WIEC_SERIAL.power_wib(ser, {slot: "off" for slot in batch})

    return batch_results



#Loops through WIB testing loop for each WIB specified by user
def run(session):
    json_data = session.json_data
    prompt_wiec_test_info(session)
    batches = make_batches(session)
    session.log(f"Testing in batches: {batches}")

    if not ptc_power.power_up(session):
        return False

    WIB_results = []
    ser = None
    try:
        ser = wib_setup.login_zynq(session)


        if ser is None:
            return False
        for b in batches:
            WIB_results.append(run_batch(session, ser, b))
    finally:
        if ser is not None:
            WIEC_SERIAL.close_serial(ser)
        ptc_power.power_down(session)
        session.datastore["wiec_wib_qc_results"] = WIB_results

    return bool(WIB_results) and all(passed for _, passed, _ in WIB_results)

    


if __name__ == "__main__":
    sys.exit(cli.run_single_item(sys.modules[__name__]))



