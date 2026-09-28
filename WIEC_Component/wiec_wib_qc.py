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

-------------------------------------------------------------------------------------------------------

"""

import json
import traceback
import subprocess
import os
import sys
import importlib

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
        print("  a) test all 6 WIB slots")
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
                    print("\nSelection invalid. Valid WIBs are 0 through 5.")
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


def run_single_wib(session, wib_number):
    """
    Runs the passthrough testing on a single WIB, including the FEMB Checkout
    Prompts the user to properly set up the hardware for next WIB slot
    """

    WIEC_utils.print_prompt_section(f"Testing WIB {wib_number}")

    session.log(f"Preparing test for WIB {wib_number}.")

    print(f"\nMove hardware to WIB {wib_number} and press Enter")

    #PLACEHOLDER FOR BETTER INSTRUCTIONS!!!!!

    input()

    # ptc_power ensures the fans are on within the function, no need to check here.
    if not ptc_power.power_up(session):
        session.log(f"WIB {wib_number} not started: PTC power-up failed.")
        return False

    try:
        setup_ok = bool(wib_setup.run(session, wib_number))
        if not setup_ok:
            session.log(f"WIB {wib_number} setup validation failed.")
            return False

        checkout_ok = bool(femb_checkout.run(session, wib_number))
        if not checkout_ok:
            session.log(f"WIB {wib_number} FEMB checkout failed.")
            return False

        session.log(f"WIB {wib_number} passed.")
        return True
    finally:
        ptc_power.power_down(session)


#Loops through WIB testing loop for each WIB specified by user
def run(session):
    json_data = session.json_data

    prompt_wiec_test_info(session)
    selected = sorted({int(w) for w in session.WIBs_testing})
    session.log(f"WIBs to be tested, in order: {selected}")

    results = {}
    for wib in selected:
        results[wib] = run_single_wib(session, wib)
        if not results[wib]:
            session.datastore["wiec_wib_qc_results"] = results
            return False

    session.datastore["wiec_wib_qc_results"] = results
    return True


if __name__ == "__main__":
    sys.exit(cli.run_single_item(sys.modules[__name__]))



