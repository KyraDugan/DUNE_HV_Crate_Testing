"""
Manages the results folder and the results files. 

-------------------------------------------------------------------------------------------------------
config.json keys used:

"WIEC_results_folder"                                Folder for the new style of WIEC results
"wiec_results_csv_name"                              Folder for the measurement csv
-------------------------------------------------------------------------------------------------------


Expected WIEC results folder currently:  "/home/dune-daq/DUNE-HV-Crate-Testing/WIEC_Component_Results"

Legacy WIEC results folder:  "/home/dune-daq/DUNE-HV-Crate-Testing/results"


Plan:   Will write to old results scheme for now, but will eventually switch to only new scheme.
        Needs:  csv for the component results
                spreadsheet for easy reading by tester
                output html for easy reading by tester
                storage of graphs for easy reading by tester

"""

import os
import re
import pandas as pd

from datetime import datetime





def file_name_safe(string):
    """
    Returns a string that is safe to use as a file name by replacing any non-desired characters with underscores.
    """
    return re.sub(r'[^A-Za-z0-9._-]', '_', string).strip("-")


def get_results_folder(session):
    """
    Returns the results folder for the WIEC component tests.  If the folder does not exist, it will be created.
    """
    config_data = session.json_data
    results_folder = config_data.get("WIEC_results_folder")
    if results_folder is None:
        raise KeyError("config.json has no 'WIEC_results_folder' entry.")
    if not os.path.exists(results_folder):
        os.makedirs(results_folder)
    return results_folder



def run_path(session):
    """
    Creates the file for the run path and returns the path name for the results folder for this run.
    """

    #Makes timestamp from the legacy code more readible, but keeps the same time
    stamp = os.path.basename(session.results_path)    
    session.datastore["legacy_stamp"] = stamp         
    started = datetime.strptime(stamp, "%Y%m%d%H%M%S")
    readable_stamp = started.strftime("%Y-%m-%d_%H-%M-%S")
    session.datastore["session_stamp"] = readable_stamp

    folder_path = get_results_folder(session)
    run_file_name = file_name_safe(readable_stamp + "_" + session.wiec_serial)

    session.datastore["run_id"] = run_file_name

    run_path = os.path.join(folder_path, "runs", run_file_name)
    os.makedirs(run_path)
    session.datastore["wiec_run_dir"] = run_path

    session.wiec_run_dir = run_path

    return run_path


CSV_COLUMNS = ["test", "measurement", "channel", "wib_slot", "femb_position", #Info for specific measurement
               "result", "value", "unit", "min", "max", "setpoint", "note",   #Info for result pass/fail
               "wiec_serial", "run_id", "start_time", "username"]             #Info for run as a whole


def measure(session, test, measurement, value, passed=None, unit="", low=None, high=None,
            wib_slot=None, femb_position=None, channel=None, 
            setpoint = None, note=""):
    """
    Creates a row with the measurement value(s) from the designated test.
    The measurement function itself should decide if the test has been passed/failed if needed.
    """

    if passed is None:
        pass_output = ""
    elif passed:
        pass_output = "PASS"
    else:
        pass_output = "FAIL"

    row = {
        "run_id": session.datastore.get("run_id", ""),
        "start_time": session.datastore.get("session_stamp", ""),
        "username": session.datastore.get("username", ""),
        "wiec_serial": session.datastore.get("wiec_serial", ""),
        "test": test,
        "result": pass_output,
        "wib_slot": wib_slot,
        "femb_position": femb_position,
        "channel": channel,
        "measurement": measurement,
        "setpoint": setpoint,
        "value": value,
        "unit": unit,
        "min": low,
        "max": high,
        "note": note
        }

    session.datastore.setdefault("measurements", []).append(row)
    return passed


def write_measurements(session):
    """
    Turns measurement rows into csv
    """

    rows = session.datastore.get("measurements", [])

    df = pd.DataFrame(rows, columns=CSV_COLUMNS)
    path = os.path.join(session.wiec_run_dir, session.json_data["wiec_results_csv_name"])
    df.to_csv(path, index=False)

    session.datastore["measurements_csv_path"] = path
    session.log(f"Measurements csv: {len(df)} rows written to {path}")

    return path

    




    
