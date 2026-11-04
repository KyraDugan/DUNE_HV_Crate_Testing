""" wib_setup.py

WIEC WIB Setup

Helper script for the wiec_wib_qc.
This validates one WIB at a time.

WIB numbering: the code counts WIBs 1-6, as on the flange slot labels. The PTC counts its
slots 0-5 (power_on_wib.py N, I2C address 0x7N, IP last octet base + N), so a WIB number is
converted with ptc_slot only where it goes to the PTC: expected_wib_ip, expected_wib_i2c_address,
power_wibs and read_wib_sensors. Call those two instead of wiec_serial.power_wib / sensors_addr.

-------------------------------------------------------------------------------------------------------
config.json keys used:

"wib_ip_prefix"                                 Expected prefix for the WIB IP addresses
"wib_ip_last_octet_base"                        Last IP octet of the WIB in PTC slot 0 (= WIB 1)
"wib_ping_timeout_seconds"                      Seconds to wait for ping before timeout
"wib_ping_count"                                Ping number
"wib_ping_expected_s"                           Expected seconds for the ping (progress bar)
"wib_expected_i2c_prefix"                       Expected prefix for i2c                    
"wib_sensor_read_wait_seconds"                  Time to wait for wib sensor readout
"wib_zynq_settle_time"                          Time to wait for zynq connection to settle
"wib_petalinux_login_expected_s"                Expected seconds until the PetaLinux login prompt (progress bar)
-------------------------------------------------------------------------------------------------------
"""

import json
import traceback
import subprocess
import os
import sys
import re
import time 
import wiec_serial as WIEC_SERIAL
from . import WIEC_utils
from . import record_results


VALID_POWER_STATES = {"on", "off"}


def ptc_slot(wib_number):
    """
    The PTC's slot number (0-5) for a WIB number (1-6, as on the flange). Only for what goes to the PTC.
    """
    return int(wib_number) - 1


def power_wibs(ser, power_map):
    """
    wiec_serial.power_wib for {WIB number (1-6): "on"/"off"}. wiec_serial counts PTC slots 0-5
    (it is shared with the legacy wiec_* scripts, so it stays that way); this converts both ways.
    Returns the power outputs keyed by WIB number.
    """
    outputs = WIEC_SERIAL.power_wib(ser, {ptc_slot(wib): state for wib, state in power_map.items()})
    return {slot + 1: output for slot, output in outputs.items()}


def read_wib_sensors(ser, power_map):
    """
    wiec_serial.sensors_addr for {WIB number (1-6): state}; converts to PTC slots and back like
    power_wibs. Returns {WIB number: {"i2cset": ..., "i2cdetect": ...}}, which check_slot reads.
    """
    outputs = WIEC_SERIAL.sensors_addr(ser, {ptc_slot(wib): state for wib, state in power_map.items()})
    return {slot + 1: output for slot, output in outputs.items()}


def expected_wib_ip(session, wib_number):
    """
    Expected ip address for the selected WIB
    """
    config_data = session.json_data
    prefix = config_data.get("wib_ip_prefix")
    base = int(config_data.get("wib_ip_last_octet_base"))
    return f"{prefix}.{base + ptc_slot(wib_number)}"


def get_wib_ip_map(session, wib_number):
    """
    Creates a dictionary of WIB number to the expected IP for this WIB
    """
    return{int(wib_number): expected_wib_ip(session, wib_number)}


def expected_wib_i2c_address(session, wib_number):
    """
    Generates the expected i2c address for the wib based on prefix
    """
    config_data = session.json_data
    i2c_prefix = config_data.get("wib_expected_i2c_prefix")
    return f"{int(i2c_prefix)}{ptc_slot(wib_number):x}"


def i2cdetect_addresses(session, output):
    """
    Extracts hex I2C addresses from the i2cdetect table, normalizes into clean hex addresses
    """
    addresses = set()

    #Splits i2cdetect output into separate lines
    for line in output.splitlines():

        #Tries to match a line that has the formatting of an i2cdetect row
        match = re.match(r"\s*([0-7][0-9a-fA-F]):\s*(.*)", line)
        if not match:
            continue

        #For a matched row, prefix becomes first character
        row_prefix = match.group(1)[0]

        #Takex everything after the colon and splits it into tokens
        cells = match.group(2).split()

        for cell in cells:
            #checks if token is 2-character hex, which is desired
            if re.fullmatch(r"[0-9a-fA-F]{2}", cell):
                addresses.add(cell.lower())

            #Checks if there is a single-character hex token, then adds row prefix
            elif re.fullmatch(r"[0-9a-fA-F]", cell):
                addresses.add(f"{row_prefix}{cell.lower()}")

    return addresses


def ping_wib(session, ip_address):
    """
    Pings the WIB, times out after time set in config.json.
    """
    config_data = session.json_data
    timeout_s = int(config_data.get("wib_ping_timeout_seconds"))
    count = int(config_data.get("wib_ping_count"))

    command = ["ping", "-c", str(count), str(ip_address)]

    try:
        # A timeout raised inside run_with_bar comes back out here, so the except below still catches it
        result = WIEC_utils.run_with_bar(
            f"Pinging {ip_address}",
            config_data["wib_ping_expected_s"],
            subprocess.run,
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(f"Ping timed out after {timeout_s} seconds for: {ip_address}")
        return False

    output = f"{result.stdout}\n{result.stderr}"
    loss_match = re.search(r"(\d+(?:\.\d+)?)%\s*packet loss", output)
    if not loss_match:
        print(f"Could not parse ping packet loss for {ip_address}.")
        if result.stderr.strip():
            print(result.stderr.strip())
        return False

    packet_loss = float(loss_match.group(1))
    return result.returncode == 0 and packet_loss == 0.0

def login_zynq(session):
    """
    Logs into the zynq
    """

    config_data = session.json_data
    WIEC_utils.countdown_sleep(session,
                    "Waiting for Zynq boot before serial login",
                    config_data.get("wib_zynq_settle_time"))
    try:
        ser = WIEC_SERIAL.login(config_data["wib_petalinux_login_expected_s"])
    except (Exception, SystemExit) as exc:
        session.log(f"Zynq serial login failed ({exc!r}): Check the USB cable and PTC power.")
        return None
    
    WIEC_utils.countdown_sleep(session, 
                    "Waiting for Zynq to settle after serial login", 
                    config_data.get("wib_zynq_settle_time"))

    return ser
    

def validate_wib_ip(session, wib_number):
    """
    Validates WIB IP address is as expected for WIB number.
    """
    ip_address = expected_wib_ip(session, wib_number)
    return ping_wib(session, ip_address)


def validate_wib_i2c_outputs(session, wib_number, sensor_outputs):
    """
    Validates WIB I2C output is as expected for WIB number.
    """
    expected_addresses = expected_wib_i2c_address(session, wib_number)

    detect_output = sensor_outputs[wib_number]["i2cdetect"]
    detected_addresses = i2cdetect_addresses(session, detect_output)
    return expected_addresses in detected_addresses


def check_slot(session, wib_number, sensor_outputs):
        """
        Checks to make sure each slot with an expected WIB has one, and that its i2c and ip are as expected.
        Records nothing, so a check that is retried is recorded only once (record_slot_checks).
        Returns (passed, reason, checks).
        passed: Pass/Fail.
        reason: reason for failure.
        checks: (measurement, value, passed, note) for each check made, for record_slot_checks.
        """

        i2c_ok = validate_wib_i2c_outputs(session, wib_number, sensor_outputs)
        checks = [("I2C address", "found" if i2c_ok else "missing", i2c_ok,
                   f"expected 0x{expected_wib_i2c_address(session, wib_number)}")]
        if not i2c_ok:
            session.log(WIEC_utils.fail_text(f"WIB {wib_number} I2C address validation failed"))
            return (False, "I2C address validation failed", checks)

        ip_ok = validate_wib_ip(session, wib_number)
        checks.append(("ping", "reply" if ip_ok else "no reply", ip_ok, expected_wib_ip(session, wib_number)))
        if not ip_ok:
            session.log(WIEC_utils.fail_text(f"WIB {wib_number} IP ping validation failed"))
            return (False, "IP ping validation failed", checks)

        return (True, "Passed I2C and ping checks", checks)


def record_slot_checks(session, wib_number, checks, attempts=1):
    """
    Copies the checks from the last check_slot of the WIB in wib_number into the measurements CSV.
    Call it once per WIB after any retry, so a retried check is not recorded twice.
    """
    for measurement, value, passed, note in checks:
        if attempts > 1:
            note = f"{note}; attempt {attempts}"
        record_results.measure(session, "wib_checkout", measurement, value, passed=passed,
                               wib_slot=wib_number, note=note)


def power_single_wib(session, wib_number):
    """
    Powers single WIB, validates IP and I2C.
    """

    config_data = session.json_data
    wib_number = int(wib_number)

    WIEC_utils.print_prompt_section(f"WIB {wib_number} powering ON")
    session.log(f"Power WIB {wib_number} ON")

    ser = login_zynq(session)

    if ser is None:
        return False
    
    try:
        power_map = {wib_number: "on"}
        power_outputs = power_wibs(ser, power_map)
        print(f"Power output for WIB {wib_number}: {power_outputs}")

        WIEC_SERIAL.run_ecat_soft_timeout_then_i2cdetect(ser)
        WIEC_utils.countdown_sleep(session, 
                        "Waiting before WIB sensor readout", 
                        config_data.get("wib_sensor_read_wait_seconds"))

        sensor_outputs = read_wib_sensors(ser, power_map)
        print(f"Sensor outputs for WIB {wib_number}: {sensor_outputs}")



    finally:
        WIEC_SERIAL.close_serial(ser)

    return True


def run(session, wib_number):
    return power_single_wib(session, wib_number)

if __name__ == "__main__":
    print("WIB_setup module is a helper and should be called by the WIEC workflow.")

    






