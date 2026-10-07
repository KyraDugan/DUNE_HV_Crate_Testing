"""
Manages the results folder and the results files. 

-------------------------------------------------------------------------------------------------------
config.json keys used:

"WIEC_results_folder"                                Folder for the new style of WIEC results

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
import json
import numpy as np
from datetime import datetime


from . import WIEC_utils, output_log

def file_name_safe(string):
    """
    Returns a string that is safe to use as a file name by replacing any non-alphanumeric characters with underscores.
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
