"""preflight.py

Environment checks and first-run guidance for the WIEC_Component top script.

-------------------------------------------------------------------------------------------------------
config.json keys used:

"caenR8033DM"                                   CAEN HV crate IP for network reachability check
"keysight970a"                                  Keysight DAQ970A IP for network reachability check
"rigol832a0"                                    Rigol DP832A #0 IP for network reachability check
"rigol832a1"                                    Rigol DP832A #1 IP for network reachability check
"PL506_IP_ADDR"                                 PL506 IP for network reachability check
"iseg_ip"                                       WIENER MPOD (iseg) IP for network reachability check
"hv_minutes_duration_long"                      Duration of each long HV test phase, minutes
"hv_minutes_duration_short"                     Duration of each short HV test phase, minutes
"simultaneous_test"                             True if all channels are tested at once
"channels_to_test"                              List of channels to test, e.g. ["1", "2", "3", "4"]
-------------------------------------------------------------------------------------------------------
"""

import importlib
import json
import os
import shutil
import subprocess
import sys

from . import WIEC_utils

STATE_FILE = ".wiec_component_state.json"

REQUIRED_PACKAGES = ["pyvisa", "openpyxl", "matplotlib", "numpy", "scipy", "pandas"]


def _ip_from_config(json_data, key):
    """Both '169.254.12.34' and 'TCPIP::169.254.9.69::INSTR' appear in config."""
    value = str(json_data.get(key, ""))
    if "::" in value:
        parts = value.split("::")
        return parts[1] if len(parts) > 1 else ""
    return value


def ping(host, timeout_s=1):
    try:
        return subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout_s), host],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout_s + 2,
        ).returncode == 0
    except Exception:
        return False


def instrument_targets(json_data):
    return [
        ("CAEN R8033DM", _ip_from_config(json_data, "caenR8033DM")),
        ("Keysight DAQ970A", _ip_from_config(json_data, "keysight970a")),
        ("Rigol DP832A #0", _ip_from_config(json_data, "rigol832a0")),
        ("Rigol DP832A #1", _ip_from_config(json_data, "rigol832a1")),
        ("Wiener PL506", _ip_from_config(json_data, "PL506_IP_ADDR")),
        ("WIENER MPOD (iseg)", _ip_from_config(json_data, "iseg_ip")),
    ]


def check_environment(config_file, ping_instruments=True):
    """Returns a list of (ok, title, detail) tuples."""
    checks = []

    # caen_r8033dm.py loads its .so from the working directory
    have_config = os.path.isfile(config_file)
    libs = [f for f in os.listdir(".") if f.startswith("libcaenhvwrapper.so")]
    checks.append((
        bool(libs),
        "Working directory",
        f"{os.getcwd()}"
        + ("" if libs else "  <-- no libcaenhvwrapper.so here, cd to the repo root"),
    ))

    checks.append((
        have_config,
        "Config file",
        config_file if have_config else f"{config_file} not found",
    ))

    # hv_module_test drives the MPOD by shelling out to net-snmp
    snmp_tools = [t for t in ("snmpget", "snmpset", "snmpwalk")
                  if shutil.which(t) is None]
    checks.append((
        not snmp_tools,
        "net-snmp tools",
        "snmpget, snmpset, snmpwalk found"
        if not snmp_tools else
        f"missing: {', '.join(snmp_tools)}  <-- needed by hv_module_test "
        "(apt install snmp)",
    ))

    # hv_module_test's limit adjustment reads the 34970A through linux-gpib,
    # which is installed system-wide and NOT in the project venv.  Without
    # this check the run gets 35 steps in before failing on the import.
    try:
        importlib.import_module("gpib")
        gpib_detail = "linux-gpib bindings available"
        gpib_ok = True
    except ImportError:
        gpib_ok = False
        gpib_detail = (
            f"no 'gpib' module in {sys.executable}  <-- the limit adjustment "
            "needs it; run with /usr/bin/python3, not the venv"
        )
    checks.append((gpib_ok, "linux-gpib (34970A)", gpib_detail))

    missing = []
    for package in REQUIRED_PACKAGES:
        try:
            importlib.import_module(package)
        except ImportError:
            missing.append(package)
    checks.append((
        not missing,
        "Python packages",
        sys.executable
        + ("" if not missing else f"  <-- missing: {', '.join(missing)}"),
    ))

    if not have_config:
        return checks

    with open(config_file) as f:
        json_data = json.load(f)

    if ping_instruments:
        for label, host in instrument_targets(json_data):
            if not host:
                checks.append((False, label, "no address in config"))
                continue
            reachable = ping(host)
            checks.append((
                reachable,
                label,
                f"{host} {'reachable' if reachable else 'NO REPLY'}",
            ))

    return checks


def print_report(checks):
    WIEC_utils.print_banner("PREFLIGHT", "preflight")
    print()
    for ok, title, detail in checks:
        print(f"  [{WIEC_utils.verdict(ok)}] {title:<20} {detail}")
    failed = [title for ok, title, _ in checks if not ok]
    if failed:
        print(WIEC_utils.fail_text(f"\n  {len(failed)} check(s) failed: {', '.join(failed)}"))
    print()
    return not failed


def estimate_minutes(json_data, items):
    minutes = 0
    for item in items:
        if item.KEY == "hv_test":
            long_phases = 4 * json_data["hv_minutes_duration_long"]
            short_phases = 4 * json_data["hv_minutes_duration_short"]
            groups = (
                1 if json_data["simultaneous_test"] == "True"
                else len(json_data["channels_to_test"])
            )
            minutes += (long_phases + short_phases + 4) * groups
        else:
            minutes += item.EST_MINUTES
    return minutes


def print_suggestions(config_file, items, json_data=None):

    print("The Warm Interface Electronics Crate (WIEC) is where the cables from the \n"
    "cold electronics in the DUNE experiment meet the warm electronics.\n")
    
    WIEC_utils.ask("Press enter to continue")

    print("This crate must be thoroughly quality control (QC) tested before it can\n"
    "be installed in the experiment, as it is expected to run for 30 years\n"
    "without major interruption.\n")

    WIEC_utils.ask("Press enter to continue")

    print("This QC test will run through each of the components on the WIEC that\n"
    "must be checked, and will return to you the results of each of these QC\n"
    "tests. If everything passes, the WIEC is ready for use!\n")

    WIEC_utils.ask("Press enter to continue")

    print("If you need to abort the test for any reason, Ctrl-C will stop all\n"
          "testing and power down everything immediately. Results will be saved.\n")

    while WIEC_utils.ask("Type: \"Ctrl-C aborts\" to continue").lower() != "ctrl-c aborts":
        print("Retype exact phrase.")

    print("The settings for the test are contained within config.json, which should not\n"
          "be altered unless confirmed by the technical coordinator.\n")
    
    while WIEC_utils.ask("Type: \"I will not alone alter config.json\" to continue").lower() != "i will not alone alter config.json":
        print("Retype exact phrase")

    print("If you have any further issues throughout the WIEC QC test,\n"
          "please contact the technical coordinator.\n")


    if json_data:
        total = estimate_minutes(json_data, items)
        chs = json_data["channels_to_test"]
        mode = (
            "all at once" if json_data["simultaneous_test"] == "True"
            else "one after another"
        )
        print(f" This config: channels {chs} tested {mode}")
        print(f" Full run of every item is roughly {total} minutes\n")


def is_first_run(root="."):
    return not os.path.isfile(os.path.join(root, STATE_FILE))


def mark_run(root=".", extra=None):
    path = os.path.join(root, STATE_FILE)
    state = {}
    if os.path.isfile(path):
        try:
            with open(path) as f:
                state = json.load(f)
        except Exception:
            state = {}
    state["runs"] = state.get("runs", 0) + 1
    if extra:
        state.update(extra)
    try:
        with open(path, "w") as f:
            json.dump(state, f, indent=4)
    except Exception:
        pass
