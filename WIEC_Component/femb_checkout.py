"""
WIEC FEMB checkout

Performs quick checkout test for FEMB boards in 4 slots.
Called by WIEC WIB QC script after WIB is validated and powered.

-------------------------------------------------------------------------------------------------------
config.json keys used:

"wib_checkout_wait_seconds"                     Wait time for WIB checkout in seconds
"femb_qc_data_root_folder"                      
"femb_checkout_default_site"                    
"wib_software_dir"                              Directory for FEMB checkout software
"wiec_femb_sets"                                Sets of FEMBs used for testing. Add more if more are available.
"wiec_femb_checkout_timeout_s"                  Time to wait for femb checkout before timeout 
"wiec_femb_limits"                              Limits for FEMB checkout to determine if pass/fail
                                                Includes:
                                                        power_V
                                                        power_A
                                                        rail_monitor_mV
                                                        monitor_path_mV
"wib_ssh_connect_timeout_s"                     Time to wait for ssh connection before timeout
"wib_startup_expected_s"                        Expected seconds for wib_startup.py (progress bar)
"wiec_femb_safety_A"                            presence_min_A: below this DC-DC current a FEMB counts as not
                                                connected; BIAS/LArASIC/ColdDATA: BNL's safety current cut
"wiec_femb_i2c_tries"                           I2C configuration retries before a FEMB is dropped
"wiec_femb_summary_max_failures"                Maximum number of failed checks to show in the summary
-------------------------------------------------------------------------------------------------------
"""

import os
import sys
import time
import csv
import tempfile
import time

from datetime import datetime, timezone
from colorama import init, Fore, Style
import json
import subprocess


from . import wib_setup, WIEC_utils, record_results

def _ssh_command(session, wib_ip, command):
    """The ssh command line that runs `command` in the checkout software folder on the WIB."""
    config_data = session.json_data
    return ["ssh", "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={config_data['wib_ssh_connect_timeout_s']}",
            f"root@{wib_ip}", f"cd {config_data['wib_software_dir']}; {command}"]


def wib_ssh_with_progress(session, wib_ip, command, timeout, stdin_text, message, expected_s):
    """
    wib_ssh for long WIB commands: the same (ok, output) result, with a progress bar while it runs.
    The output goes to a temporary file rather than a pipe: a pipe holds only ~64 KB, and a full
    pipe would leave ssh and this program waiting for each other forever.
    """
    with tempfile.TemporaryFile(mode="w+") as output:
        process = subprocess.Popen(_ssh_command(session, wib_ip, command), stdin=subprocess.PIPE,
                                   stdout=output, stderr=subprocess.STDOUT, text=True)
        try:
            if stdin_text:
                process.stdin.write(stdin_text)
            process.stdin.close()

            start = time.time()
            while process.poll() is None:            # None = still running
                elapsed = time.time() - start
                if elapsed > timeout:
                    process.kill()
                    process.wait()
                    print()
                    output.seek(0)
                    return False, output.read() + f"\n[timed out after {timeout} s]"
                WIEC_utils.draw_bar(message, elapsed, expected_s)
                time.sleep(1)
            WIEC_utils.finish_bar(message)
        finally:
            if process.poll() is None:               # Ctrl-C or an error: do not leave ssh running
                process.kill()

        output.seek(0)
        return process.returncode == 0, output.read()


def wib_ssh(session, wib_ip, command, timeout, stdin_text=None):
    """
    Runs command inside BNL_CE_WIB_SW_QC on the WIB, handles timeout 
    """
    config_data = session.json_data
    WIB_SW_DIR = config_data.get("wib_software_dir")
    wib_timeout = config_data.get("wib_ssh_connect_timeout_s")
    ssh_command = ["ssh", 
                   "-o", 
                   "BatchMode = yes", 
                   "-o", 
                   f"ConnectTimeout={wib_timeout}", 
                   f"root@{wib_ip}",
                   f"cd {WIB_SW_DIR}; {command}",
                   ]

    try:
        result = subprocess.run(ssh_command, input = stdin_text, capture_output = True, text = True, timeout = timeout)
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout or ""
        if isinstance(partial, bytes):
            partial = partial.decode(errors="replace")
        return False, partial + f"\n[timed out after {timeout} s]"
    return result.returncode == 0, result.stdout + result.stderr


def get_femb_sets(session):
    """
    Make sure FEMBs available variable in config.json is a proper value
    """


    positions = session.json_data["wib_femb_positions"]
    femb_sets = session.json_data.get("wiec_femb_sets")
    if not isinstance(femb_sets, list) or not femb_sets:
        raise ValueError(
            f"wiec_femb_sets is {femb_sets!r}. It must be a list with at least one FEMB set."
        )

    for number, femb_set in enumerate(femb_sets):
        if not isinstance(femb_set, list) or len(femb_set) != positions:
            raise ValueError(
                f"FEMB set {number} is {femb_set!r}. Each set must list exactly "
                f'{positions} serial numbers (use "" for an empty position).'
            )
        if not all(isinstance(serial, str) for serial in femb_set):
            raise ValueError(f"FEMB set {number} is {femb_set!r}. Serial numbers must be strings.")
        if not any(serial.strip() for serial in femb_set):
            raise ValueError(f"FEMB set {number} has no serial numbers. At least one position must be filled.")

    return [[serial.strip() for serial in femb_set] for femb_set in femb_sets]


def fembs_off(session, wib_number):
    """
    Turns off power to all 4 FEMBs on the WIB in the specified slot.  
    Returns True only if the WIB script confirms it finished successfully.
    """
    wib_ip = wib_setup.expected_wib_ip(session, wib_number)
    off = " ".join(["off"] * session.json_data["wib_femb_positions"])
    wib_ok, wib_output = wib_ssh(session, wib_ip, f"python3 top_femb_powering.py {off}",
                                 session.json_data["wib_command_timeout_s"])

    if wib_ok and "Done" in wib_output:
        session.log(f"WIB {wib_number} FEMBs powered off successfully.")
        return True
    
    session.log(WIEC_utils.fail_text(f"WIB {wib_number} FEMB power off failed. FEMBs may still be powered."))
    print(Fore.RED + "Check the FEMBs are off before moving any cables" + Style.RESET_ALL)
    print(wib_output)
    return False


def prepare_wib(session, wib_number):
    """
    Sets the WIB clock and runs wib_startup.py on the WIB in the specified slot.
    Must run once after each WIB power cycle, before the FEMB checkout.
    Returns True only if both steps succeed.
    """
    wib_ip = wib_setup.expected_wib_ip(session, wib_number)
    config_data = session.json_data

    # Step 1: set the WIB clock. The WIB has no battery clock, so its date is wrong after power up
    now = datetime.now(timezone.utc)
    formatted_now = now.strftime('%a %b %d %H:%M:%S UTC %Y')
    wib_ok, wib_output = wib_ssh(session, wib_ip, f"date -s '{formatted_now}'", config_data["wib_command_timeout_s"])
    if not wib_ok:
        session.log(WIEC_utils.fail_text(f"WIB {wib_number} clock sync failed. Check the SSH connection to {wib_ip}."))
        print(wib_output)
        return False
    session.log(f"WIB {wib_number} clock set to {wib_output.strip()}")

    # Step 2: initialize WIB timing and I2C. Prints "Done" when finished
    wib_ok, wib_output = WIEC_utils.run_with_bar(f"WIB {wib_number}: running wib_startup.py",
                                                 config_data["wib_startup_expected_s"],
                                                 wib_ssh, session, wib_ip, "python3 wib_startup.py",
                                                 config_data["wib_startup_timeout_s"])
    if wib_ok and "Done" in wib_output:
        session.log(f"WIB {wib_number} startup complete.")
        return True

    session.log(WIEC_utils.fail_text(f"WIB {wib_number} startup failed."))
    print(wib_output)
    return False


RESULT_MARKER = "WIEC_RESULT_JSON"                                  # must match RESULT_MARKER in wiec_femb_chk.py
# Must match TEST_NAMES in wiec_femb_chk.py (all of BNL's warm tests, in the order they run).
TEST_NAMES = ("power", "registers", "noise", "noise_peaking_times", "se_power", "se_rails",
              "se_pulse", "diff_pulse", "diff_power", "diff_rails", "monitor_path")

# How each kind of test is graded: test name -> the label used in the check names.
POWER_TESTS = {"power": "initial", "se_power": "SE", "diff_power": "DIFF"}        # power_V / power_A limits
RAIL_TESTS = {"se_rails": "SE", "diff_rails": "DIFF"}                             # rail_monitor_mV limits
DATA_TESTS = {"noise": "noise", "noise_peaking_times": "noise peaking times",     # BNL verdict, plus optional
              "se_pulse": "SE pulse", "diff_pulse": "DIFF pulse"}                 # wiec_femb_limits[test] limits

# The wiec_femb_limits sections a switched-on test cannot be graded without.
REQUIRED_LIMITS = {"power": ["power_V", "power_A"], "se_power": ["power_V", "power_A"],
                   "diff_power": ["power_V", "power_A"], "se_rails": ["rail_monitor_mV"],
                   "diff_rails": ["rail_monitor_mV"], "monitor_path": ["monitor_path_mV"]}


def script_path(session):
    """Full path of the WIB-side script, which lives next to this file."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), session.json_data["wiec_femb_script"])


def get_femb_tests(session):
    """
    Reads wiec_femb_tests from the config: which parts of the FEMB checkout to run.
    A test missing from the config counts as off. Returns {test name: True/False}.
    """
    tests = session.json_data.get("wiec_femb_tests")
    if not isinstance(tests, dict):
        raise ValueError(f"wiec_femb_tests is {tests!r}. It must map test names to true/false, "
                         f"for the tests {list(TEST_NAMES)}.")
    unknown = [name for name in tests if name not in TEST_NAMES]
    if unknown:
        raise ValueError(f"wiec_femb_tests has unknown tests {unknown}. Known tests: {list(TEST_NAMES)}.")
    selected = {name: bool(tests.get(name, False)) for name in TEST_NAMES}
    if not any(selected.values()):
        raise ValueError("wiec_femb_tests switches every test off. Switch at least one on.")

    limits = session.json_data.get("wiec_femb_limits", {})
    for test, sections in REQUIRED_LIMITS.items():
        missing = [section for section in sections if section not in limits]
        if selected[test] and missing:
            raise ValueError(f"wiec_femb_tests switches {test} on, but wiec_femb_limits has no {missing}.")
    return selected


def femb_settings(session, femb_ids):
    """The settings wiec_femb_chk.py needs. The WIB cannot read config.json, so they go to it on stdin."""
    config_data = session.json_data
    return {
        "tests": get_femb_tests(session),
        "femb_ids": {position: serial for position, serial in enumerate(femb_ids) if serial},
        "dc2dc_V": config_data["wiec_femb_dc2dc_V"],
        "safety_A": config_data["wiec_femb_safety_A"],
        "monitor_samples": config_data["wiec_femb_monitor_samples"],
        "monitor_lsb_mV": config_data["wiec_femb_monitor_lsb_mV"],
        "data_samples": config_data["wiec_femb_data_samples"],
        "settle_s": config_data["wiec_femb_settle_s"],
        "toy_tpc": config_data["wiec_femb_toy_tpc"],
        "i2c_tries": config_data["wiec_femb_i2c_tries"],
    }


def copy_script_to_wib(session, wib_number):
    """
    Copies wiec_femb_chk.py from this repo into the checkout software folder on the WIB,
    so the WIB always runs the current version. Returns True if the copy worked.
    """
    config_data = session.json_data
    wib_ip = wib_setup.expected_wib_ip(session, wib_number)
    connect_timeout = config_data.get("wib_ssh_connect_timeout_s")
    destination = f"root@{wib_ip}:{config_data.get('wib_software_dir')}/"
    command = ["scp", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={connect_timeout}",
               script_path(session), destination]

    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=config_data["wib_command_timeout_s"])
    except subprocess.TimeoutExpired:
        session.log(f"Copying {config_data['wiec_femb_script']} to WIB {wib_number} timed out.")
        return False
    if result.returncode != 0:
        session.log(WIEC_utils.fail_text(f"Copying {config_data['wiec_femb_script']} to WIB {wib_number} failed: {result.stderr.strip()}"))
        return False
    return True


def parse_result(output):
    """
    Finds the WIEC_RESULT_JSON line printed by wiec_femb_chk.py and turns it back into Python.
    JSON keys are always text, so the FEMB positions ("0", "1", ...) are turned back into ints.
    Returns the result dict, or None if the script never printed a complete result.
    """
    for line in reversed(output.splitlines()):
        line = line.strip()
        if not line.startswith(RESULT_MARKER):
            continue
        try:
            result = json.loads(line[len(RESULT_MARKER):])
        except ValueError:          # a cut-off line is not valid JSON
            return None
        for section in TEST_NAMES + ("dropped_reasons",):
            result[section] = {int(position): values for position, values in result[section].items()}
        return result
    return None


def run_assembly_checkout(session, wib_number, femb_ids):
    """
    Runs wiec_femb_chk.py on the WIB for every FEMB position that has a serial number.
    Returns (result, output): result is the parsed measurements, or None if the script did not finish.
    """

    config_data = session.json_data
    wib_ip = wib_setup.expected_wib_ip(session, wib_number)
    positions = [position for position, serial in enumerate(femb_ids) if serial]

    # rm -rf CHK first: BNL's analysis writes its plots there, and old plots must not be mistaken for new ones.
    # `timeout` ends the script on the WIB itself at the deadline (it switches the FEMBs off on the
    # way out); the PC waits a little longer, so the WIB side always stops first.
    checkout_timeout = config_data["wiec_femb_checkout_timeout_s"]
    command = (f"rm -rf CHK; timeout {checkout_timeout} python3 {config_data['wiec_femb_script']} "
               + " ".join(str(position) for position in positions))
    settings = femb_settings(session, femb_ids)
    session.log(f"WIB {wib_number}: FEMB checkout on positions {positions}, tests "
                f"{[name for name, on in settings['tests'].items() if on]}")
    wib_ok, wib_output = wib_ssh_with_progress(
        session, wib_ip, command, checkout_timeout + config_data["wib_command_timeout_s"],
        json.dumps(settings), f"WIB {wib_number} FEMB checkout", config_data["wiec_femb_checkout_expected_s"])


    # Keep the full output: it is the first place to look when a checkout fails.
    output_file = os.path.join(session.results_path, f"femb_checkout_wib{wib_number}.txt")
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(wib_output)

    result = parse_result(wib_output)
    if result is None:
        session.log(f"WIB {wib_number}: the FEMB checkout did not finish. Its output is in {output_file}")
    elif result.get("report_dir"):
        fetch_report(session, wib_number, result["report_dir"])
    return result, wib_output


def fetch_report(session, wib_number, report_dir):
    """
    Copies BNL's report folder (plots from the ADC data tests) back into this run's results folder,
    as results/<stamp>/femb_checkout_wib<N>_report/. Returns the local path, or None.
    """
    config_data = session.json_data
    wib_ip = wib_setup.expected_wib_ip(session, wib_number)
    destination = os.path.join(session.results_path, f"femb_checkout_wib{wib_number}_report")
    command = ["scp", "-r", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={config_data['wib_ssh_connect_timeout_s']}",
               f"root@{wib_ip}:{config_data['wib_software_dir']}/{report_dir}", destination]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=config_data["wib_command_timeout_s"])
    except subprocess.TimeoutExpired:
        session.log(f"WIB {wib_number}: copying the FEMB checkout plots timed out")
        return None
    if result.returncode != 0:
        session.log(WIEC_utils.fail_text(f"WIB {wib_number}: copying the FEMB checkout plots failed: {result.stderr.strip()}"))
        return None
    return destination


def _in_range(value, low, high):
    """True if value is a number between low and high (inclusive)."""
    return isinstance(value, (int, float)) and low <= value <= high


def grade(result, limits, femb_ids):
    """
    Compares the measurements from wiec_femb_chk.py with wiec_femb_limits.
    Returns a list of checks, one dict per measured value, e.g.
        {"femb": "FEMB_0", "position": 0, "measurement": "LArASIC voltage",
         "value": 2.95, "unit": "V", "min": 2.8, "max": 3.2, "passed": True}
    """
    checks = []

    def add(position, measurement, value, unit, limit=None, passed=None):
        low, high = limit if limit else (None, None)
        if passed is None:
            passed = _in_range(value, low, high) if limit else True   # no limit set: recorded only
        checks.append({"femb": femb_ids[position], "position": position, "measurement": measurement,
                       "value": value, "unit": unit, "min": low, "max": high, "passed": bool(passed)})

    tests = result["tests"]
    for position in result["positions"]:
        # Power: initial power is measured for every FEMB, including one the safety check switches off.
        for test, label in POWER_TESTS.items():
            if tests[test] and position in result[test]:
                for rail, reading in result[test][position].items():
                    add(position, f"{label} {rail} voltage", reading["V"], "V", limits["power_V"].get(rail))
                    add(position, f"{label} {rail} current", reading["A"], "A", limits["power_A"].get(rail))

        # The safety cut always runs on the WIB; a FEMB it switched off is not tested further.
        if position in result["dropped"]:
            reasons = ", ".join(result["dropped_reasons"].get(position, []))
            add(position, "connection / safety check", f"switched off ({reasons})", "", passed=False)
            continue

        if tests["registers"]:
            for chip, correct in result["registers"][position].items():
                add(position, f"{chip} registers", "correct" if correct else "wrong", "", passed=correct)

        for test, label in RAIL_TESTS.items():
            if tests[test]:
                rails = result[test][position]
                for name, limit in limits["rail_monitor_mV"].items():
                    add(position, f"{label} {name} rail", rails.get(name), "mV", limit)   # missing -> fails
                for name, value in rails.items():
                    if name not in limits["rail_monitor_mV"]:
                        add(position, f"{label} {name} rail", value, "mV")                # recorded only

        if tests["monitor_path"]:
            monitor = result["monitor_path"][position]
            for name, limit in limits["monitor_path_mV"].items():
                values = monitor.get(name)
                if not values:
                    add(position, f"{name} monitor", None, "mV", limit)                  # missing -> fails
                    continue
                for chip, value in enumerate(values):
                    add(position, f"ASIC {chip} {name}", value, "mV", limit)

        # ADC data tests: BNL's own verdict, plus any limits set in wiec_femb_limits[<test>].
        for test, label in DATA_TESTS.items():
            if tests[test]:
                outcome = result[test][position]
                add(position, f"{label} (BNL verdict)", "pass" if outcome["bnl_pass"] else "fail", "",
                    passed=outcome["bnl_pass"])
                test_limits = limits.get(test, {})
                for name, value in outcome["values"].items():
                    add(position, f"{label} {name}", value, "ADC", test_limits.get(name))

    return checks


def femb_set_for_slot(femb_sets, batch, wib_number):
    """
    Which FEMB set the WIB in wib_number uses, and which slot that set has to be moved from.
    The k-th slot of a batch uses set k % len(femb_sets). With enough sets every WIB has its
    own and move_from is None; with fewer, a set is reused and move_from is the slot that
    used it last. Returns (set_number, femb_ids, move_from).
    """
    index = batch.index(wib_number)
    set_number = index % len(femb_sets)
    move_from = batch[index - len(femb_sets)] if index >= len(femb_sets) else None
    return set_number, femb_sets[set_number], move_from


def describe_check(check):
    """
    One readable line for a check: what was measured, the value, and the allowed range if it has one.
    """
    text = f"{check['measurement']} = {check['value']} {check['unit']}".rstrip()
    if check["min"] is not None:
        text += f"  (allowed {check['min']} to {check['max']})"
    return text


def record_measurements(session, wib_number, attempts=1):
    """
    Copies the last FEMB checkout of the WIB in wib_number into the measurements CSV, one row per
    check. Call it once per WIB after any retries, so a retried checkout is not recorded twice.
    A checkout that did not finish becomes one failed row with the reason.
    """
    record = session.datastore.get("femb_checkout", {}).get(wib_number)
    if record is None:
        return
    note = f"attempt {attempts}" if attempts > 1 else ""
    if not record["checks"]:
        record_results.measure(session, "femb_checkout", "checkout completed", "no",
                               passed=False, wib_slot=wib_number, note=record["reason"])
        return
    for check in record["checks"]:
        record_results.measure(session, "femb_checkout", check["measurement"], check["value"],
                               passed=check["passed"], unit=check["unit"],
                               low=check["min"], high=check["max"], wib_slot=wib_number,
                               femb_position=check["position"], femb_serial=check["femb"], note=note)


def print_femb_results(session, wib_number):
    """
    Shows the operator the result of the last FEMB checkout of the WIB in wib_number:
    one line per FEMB (PASS/FAIL, how many checks passed) and, for a failed FEMB, why.
    If the checkout did not finish, shows the reason and the last lines of the WIB's output.
    """
    record = session.datastore.get("femb_checkout", {}).get(wib_number)
    if record is None:
        return
    max_lines = session.json_data["wiec_femb_summary_max_failures"]
    WIEC_utils.print_prompt_section(f"WIB {wib_number} FEMB checkout results")

    checks = record["checks"]
    if not checks:
        print(WIEC_utils.color(f"  The checkout did not complete: {record['reason']}", Fore.RED))
        output_file = os.path.join(session.results_path, f"femb_checkout_wib{wib_number}.txt")
        if os.path.isfile(output_file):
            with open(output_file, encoding="utf-8") as f:
                last_lines = f.read().strip().splitlines()[-max_lines:]
            print(f"  Last lines of the WIB output ({output_file}):")
            for line in last_lines:
                print(f"      {line}")
        return

    for position in sorted({check["position"] for check in checks}):
        femb_checks = [check for check in checks if check["position"] == position]
        failed = [check for check in femb_checks if not check["passed"]]
        verdict = WIEC_utils.verdict(not failed)
        print(f"  FEMB position {position} ({femb_checks[0]['femb']}): {verdict}   "
              f"{len(femb_checks) - len(failed)} of {len(femb_checks)} checks passed")
        for check in failed[:max_lines]:
            print(f"      {describe_check(check)}")
        if len(failed) > max_lines:
            print(f"      ... and {len(failed) - max_lines} more failed checks (all are in the results JSON)")


def run(session, wib_number, femb_ids, start_wib=True):
    """
    FEMB pass-through checkout of the WIB in wib_number, using the FEMBs in femb_ids.
    Returns (passed, reason), like wib_setup.check_slot.
    """
    limits = session.json_data.get("wiec_femb_limits")
    record = {"femb_ids": list(femb_ids), "passed": False, "reason": "", "checks": []}
    session.datastore.setdefault("femb_checkout", {})[wib_number] = record   # stored before anything can fail

    if not copy_script_to_wib(session, wib_number):
        record["reason"] = f"could not copy {session.json_data['wiec_femb_script']} to the WIB"
        return False, record["reason"]

    try:
        if start_wib and not prepare_wib(session, wib_number):
            record["reason"] = "WIB clock sync or wib_startup.py failed"
            return False, record["reason"]
        result, _ = run_assembly_checkout(session, wib_number, femb_ids)
    finally:
        fembs_off(session, wib_number)       # the script switches them off too; this makes sure

    if result is None:
        record["reason"] = f"the checkout script did not finish (see femb_checkout_wib{wib_number}.txt)"
        return False, record["reason"]

    checks = grade(result, limits, femb_ids)
    record["checks"] = checks
    failed = [check for check in checks if not check["passed"]]
    record["passed"] = not failed

    if failed:
        record["reason"] = WIEC_utils.fail_text(f"{len(failed)} of {len(checks)} FEMB checks failed")
        return False, record["reason"]

    record["reason"] = WIEC_utils.pass_text(f"all {len(checks)} FEMB checks passed")
    session.log(f"WIB {wib_number}: {record['reason']}")
    return True, record["reason"]