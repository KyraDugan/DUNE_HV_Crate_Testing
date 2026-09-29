""" WIEC WIB Setup

Helper script for the wiec_wib_qc.
This validates one WIB at a time.

-------------------------------------------------------------------------------------------------------
config.json keys used:

"wib_ip_prefix"                                 Expected prefix for the WIB IP addresses
"wib_ip_last_octet_base"                        Expected base for WIB IP last octet
"wib_ping_timeout_seconds"                      Seconds to wait for ping before timeout
"wib_ping_count"                                Ping number
"wib_expected_i2c_prefix"                       Expected prefix for i2c                    
"wib_sensor_read_wait_seconds"                  Time to wait for wib sensor readout
"wib_zynq_settle_time"                          Time to wait for zynq connection to settle

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
import wiec_output_log

from . import WIEC_utils


VALID_POWER_STATES = {"on", "off"}


def expected_wib_ip(session, wib_number):
    """
    Expected ip address for the selected WIB
    """
    config_data = session.json_data
    prefix = config_data.get("wib_ip_prefix")
    base = int(config_data.get("wib_ip_last_octet_base"))
    return f"{prefix}.{base + int(wib_number)}"


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
    return f"{int(i2c_prefix)}{int(wib_number):x}"


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
        result = subprocess.run(
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
        ser = WIEC_SERIAL.login()
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
        Returns [boolean, string].   
        boolean: Pass/Fail.   
        string: reason for failure.   
        """

        i2c_ok = validate_wib_i2c_outputs(session, wib_number, sensor_outputs)
        if not i2c_ok:
            session.log(f"WIB {wib_number} I2C address validation failed")
            return (False, "I2C address validation failed")

        ip_ok = validate_wib_ip(session, wib_number)
        if not ip_ok:
            session.log(f"WIB {wib_number} IP ping validation failed")
            return (False, "IP ping validation failed")

        return(True, "Passed I2C and ping checks")


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
        power_outputs = WIEC_SERIAL.power_wib(ser, power_map)
        print(f"Power output for WIB {wib_number}: {power_outputs}")

        WIEC_SERIAL.run_ecat_soft_timeout_then_i2cdetect(ser)
        WIEC_utils.countdown_sleep(session, 
                        "Waiting before WIB sensor readout", 
                        config_data.get("wib_sensor_read_wait_seconds"))

        sensor_outputs = WIEC_SERIAL.sensors_addr(ser, power_map)
        print(f"Sensor outputs for WIB {wib_number}: {sensor_outputs}")



    finally:
        WIEC_SERIAL.close_serial(ser)

    return True


def run(session, wib_number):
    return power_single_wib(session, wib_number)

if __name__ == "__main__":
    print("WIB_setup module is a helper and should be called by the WIEC workflow.")

    






