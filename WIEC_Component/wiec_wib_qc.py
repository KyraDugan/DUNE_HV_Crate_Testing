""" WIEC WIB QC Test Script

This is the top-level script for the wiec wib test.
This will work sequentially through the desired WIBs being tested.
The hardware will need to be moved between each WIB test, handled here.

runs under run_wiec_component.py umbrella
utilizes wib_setup.py, femb_checkout.py, and ptc_power.py.

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


NAME = "WIEC WIB QC test"
KEY = "wiec_wib_qc"
DESCRIPTION = "Sequential WIB powering and FEMB checkout to test Flange Board"
EST_MINUTES = 30
RESULTS_FILE = "results.json"

VALID_WIB_POWER_STATES = ["on", "off"]


#Asks the user which WIBs will be tested
def prompt_wiec_test_info(session):
    print_prompt_section("WIEC QC : Test Info")

    WIB_input_check = False

    while WIB_input_check == False:
        print("Which WIBs will you test?")
        print("  a) test all 6 WIB slots")
        print("  s) select which WIBs to be tested")
        WIB_choice = input().strip().lower()

        if WIB_choice in ("a", "all"):
            WIBs_to_test = list(range(6))
            WIB_input_check = True
            continue

        elif WIB_choice in ("s", "select"):
            WIB_selection_check = False

            while WIB_selection_check == False:
                WIB_input = [int(x) for x in input("Enter WIBs to be tested separated by spaces (Ranging from 0 to 5): ").split()]
                possible_WIBs = set(range(6))
                WIB_match = set(WIB_input).issubset(possible_WIBs)

                if WIB_match == True:
                    WIBs_to_test = WIB_input
                    WIB_selection_check = True
                    WIB_input_check = True
                    continue

                elif WIB_match == False:
                    print("Selection invalid. Reinput WIB selection: ")
                    print("Example: If you want to test WIBs 0, 2, and 3, enter \"0 2 3\" \n\n")
                    continue

        else:
            print("Selection invalid. Reinput test selection: \n\n")
            continue


    session.WIBs_testing = WIBs_to_test


def run_single_wib(session, wib_number):
    session.log(f"Preparing test for WIB {wib_number}.")

    print(f"\nMove hardware to WIB {wib_number} and press Enter")

    """
    NEED TO CREATE A POPUP WITH GOOD INSTRUCTIONS FOR EACH WIB!!!
    THIS IS A PLACEHOLDER!!!
    """

    setup_ok = bool(wib_setup.run(session, wib_number = wib_number))
    if setup_ok == False:
        session.log(f"WIB {wib_number} setup failed.")
        return False

    checkout_ok = femb_checkout.run(session, wib_number = wib_number)
    if checkout_ok == False:
        session.log(f"WIB {wib_number} FEMB checkout failed.")
        return False

    session.log(f"WIB {wib_number} passed.")

    return True


#Shuts down the ptc and everything else too
def shutdown_all():
    print("\nRunning final shutdown logic...")

    try:
        ptc_power.power_down
    except BaseException:
        print("PTC shutdown raised an exception.")
        print(traceback.format_exc())
    
#Creates section title
def print_prompt_section(title):
    print("\n" + "-" * 15 + f" {title} " + "-" * 15)



#Loops through WIB testing loop for each WIB specified by user
def run(session):

    json_data = session.json_data
    test_name = session.test_name

    prompt_wiec_test_info(session)
    selected = sorted({int(w) for w in session.WIBs_testing})
    session.log(f"Selected WIBs in testing order: {selected}")


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



