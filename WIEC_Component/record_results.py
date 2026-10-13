""" record_results.py

Manages the results folder and the results files. 

-------------------------------------------------------------------------------------------------------
config.json keys used:

"WIEC_results_folder"                               Folder for the WIEC results
"wiec_results_csv_name"                             Name of measurement .csv
"wiec_qc_sheet_name"                                Name of the wiec qc spreadsheet output
"wiec_report_name"                                  File name for the .html report
"wiec_plots_folder"                                 Folder name for the created plots
"wiec_raw_folder"                                   Folder name for the raw recorded data
-------------------------------------------------------------------------------------------------------


Expected WIEC results folder:  "/home/dune-daq/DUNE-HV-Crate-Testing/WIEC_Component_Results"
Legacy WIEC results folder:  "/home/dune-daq/DUNE-HV-Crate-Testing/results"

"""

import os
import re
import pandas as pd
import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


from datetime import datetime
from . import WIEC_utils


# Cell background colour for each result in the QC sheet (pale green / pale red, Excel's own good/bad shades)
QC_SHEET_FILLS = {"PASS": f"{WIEC_utils.PASS_CELL}", "FAIL": f"{WIEC_utils.FAIL_CELL}", "not tested": f"{WIEC_utils.NOT_TESTED_CELL}"}



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


CSV_COLUMNS = ["test", "measurement", "channel", "wib_slot", "femb_position", "femb_serial", #Info for specific measurement
               "result", "value", "unit", "min", "max", "setpoint", "note",   #Info for result pass/fail
               "wiec_serial", "run_id", "start_time", "username"]             #Info for run as a whole


def measure(session, test, measurement, value, passed=None, unit="", low=None, high=None,
            wib_slot=None, femb_position=None, femb_serial=None, channel=None, 
            setpoint = None, note="", plot=None, plot_title=None):
    """
    Creates a row with the measurement value(s) from the designated test.
    The measurement function itself should decide if the test has been passed/failed if needed.
    plot is the file name of the PNG in plots/<test>/ that shows this measurement, and plot_title
    its heading in the summary; the summary puts each plot next to the rows that name it, shows a
    failed row's plot and folds the rest. Both go in run.json, not the CSV.
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
        "femb_serial": femb_serial,
        "channel": channel,
        "measurement": measurement,
        "setpoint": setpoint,
        "value": value,
        "unit": unit,
        "min": low,
        "max": high,
        "note": note,
        "plot": plot,
        "plot_title": plot_title
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


def test_folder(session, kind, test):
    """
    Creates or finds folder for the type of test being created.
    """
    config_data = session.json_data
    folder_name = config_data[f"wiec_{kind}_folder"]
    run_dir = getattr(session, "wiec_run_dir", None) or session.results_path
    path = os.path.join(run_dir, folder_name, test)
    if not os.path.exists(path):
        os.makedirs(path)
    return path 


            
def failure_summary(session):
    """
    Returns a short summary of what failed, for the "why" column of the QC sheet.
    One entry per place (test, WIB slot, FEMB position), listing each failed measurement once:
        "heater_test: temperature rise; hv_test: pos_term resistance, neg_term resistance"
    Returns "" if nothing failed.
    """

    measurements = session.datastore.get("measurements", [])
    failed_tests = [r for r in measurements if r["result"] == "FAIL"]

    reasons = {}

    for row in failed_tests:
        label = row["test"]
        if row["wib_slot"] is not None:
            label += f" WIB {row['wib_slot']}"
        if row["femb_position"] is not None:
            label += f" FEMB {row['femb_position']}"

        names = reasons.setdefault(label, [])
        if row["measurement"] not in names:
            names.append(row["measurement"])

    summary = []
    for label, names in reasons.items():
        summary.append(f"{label}: {', '.join(names)}")

    return "\n".join(summary)



def qc_sheet_row(session, items):
    """
    Returns one row of the QC sheet as a dict from column name to value, in column order:
    run information, PASS/FAIL/not tested for each item, then a short "Why" for any failures.
    """

    datastore = session.datastore
    overall = datastore.get("overall")

    row = {
        "WIEC Serial": datastore.get("wiec_serial", ""),
        "Overall Result": overall.upper() if overall else "not recorded",
        "Start Time": datastore.get("session_stamp", ""),
        "Run Folder": datastore.get("run_id", ""),
        "User": datastore.get("username", ""),
    }

    for item in items:
        passed = session.results.get(item.KEY)
        if passed is None:
            row[item.NAME] = "not tested"
        elif passed:
            row[item.NAME] = "PASS"
        else:
            row[item.NAME] = "FAIL"

    why = failure_summary(session)
    if not why and "FAIL" in row.values():
        why = "No failed checks recorded, see session_log.txt"
    row["Why"] = why

    return row


def write_qc_sheet(session, items):
    """
    Adds this run's row to the QC sheet (one row per run, PASS/FAIL per item and a short why).
    Creates the sheet with a header row the first time. A column that is not in the sheet yet
    (e.g. a new item) is added at the end; older rows stay blank in it.
    """

    row = qc_sheet_row(session, items)
    path = os.path.join(get_results_folder(session), session.json_data["wiec_qc_sheet_name"])

    if os.path.exists(path):
        workbook = openpyxl.load_workbook(path)
        sheet = workbook.active
    else:
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "WIEC QC"
        sheet.freeze_panes = "B2"

    # Column name -> column number, from the header row
    columns = {cell.value: cell.column for cell in sheet[1] if cell.value is not None}

    for name in row:
        if name not in columns:
            column = max(columns.values(), default=0) + 1
            header = sheet.cell(row=1, column=column, value=name)
            header.font = Font(bold=True)
            header.alignment = Alignment(wrap_text=True, vertical="top")
            width = 80 if name == "Why" else max(12, len(name) + 2, len(str(row[name])) + 2)
            sheet.column_dimensions[get_column_letter(column)].width = width
            columns[name] = column

    # Links are relative to the sheet, so they keep working if the results folder is copied or moved
    links = {}
    if getattr(session, "wiec_run_dir", None):
        run_folder = os.path.relpath(session.wiec_run_dir, os.path.dirname(path))
        links["WIEC Serial"] = os.path.join(run_folder, session.json_data["wiec_report_name"])
        links["Run Folder"] = run_folder

    row_number = sheet.max_row + 1
    for name, value in row.items():
        cell = sheet.cell(row=row_number, column=columns[name], value=value)
        if name in links:
            cell.hyperlink = links[name]
            cell.style = "Hyperlink"

        cell.alignment = Alignment(wrap_text=True, vertical="top")
        if value in QC_SHEET_FILLS:
            cell.fill = PatternFill(fill_type="solid", start_color=QC_SHEET_FILLS[value])

    lines = row["Why"].count("\n") + 1
    sheet.row_dimensions[row_number].height = 15 * lines

    workbook.save(path)
    session.datastore["qc_sheet_path"] = path
    session.log(f"QC sheet: row {row_number} written to {path}")
    return path


