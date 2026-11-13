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

import subprocess, re
import wiec_serial as WIEC_SERIAL
from . import WIEC_utils, record_results

VALID_POWER_STATES = {"on", "off"}


def ptc_slot(wib_number):
    """
    The PTC's slot number (0-5) for a WIB number (1-6, as on the flange). Only for what goes to the PTC.
    """
    return int(wib_number) - 1


def power_wibs(session, ser, power_map):
    """
    Switches WIBs {WIB number (1-6): "on"/"off"} with power_on_wib.py on the PTC (which counts
    slots 0-5). After "off", waits until the WIB stops answering ping. Returns the command
    outputs keyed by WIB number.
    """
    outputs = {}
    for wib, state in power_map.items():
        outputs[wib] = WIEC_SERIAL.run_petalinux_command(ser, f"python3 power_on_wib.py {ptc_slot(wib)} {state}")
        print(f"WIB {wib} {state}: {outputs[wib].strip()}")

    limit = session.json_data["wib_power_off_limit_s"]
    for wib, state in power_map.items():
        if state != "off":
            continue
        ip = expected_wib_ip(session, wib)
        gone, _ = WIEC_utils.wait_until(f"WIB {wib}: waiting for power-off", lambda: not ping_once(ip), limit)
        if not gone:
            session.log(WIEC_utils.fail_text(f"WIB {wib} still answers ping {limit} s after power-off. "
                                             "Check it is off before moving it."))
    return outputs


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
    Pings the WIB until it answers (it boots in about 30 s), up to wib_boot_limit_s.
    """
    config = session.json_data
    answered, seconds = WIEC_utils.wait_until(f"Waiting for {ip_address} to answer ping",
                                              lambda: ping_once(ip_address),
                                              config["wib_boot_limit_s"], 1,
                                              expected_s=config["wib_boot_expected_s"])
    if answered:
        session.log(f"{ip_address} answered ping after {seconds:.0f} s")
    return bool(answered)


ECAT_COMMAND = "python3 ecat_test1b.py logfile.txt"
ECAT_PASS_DONE = "switching back to default I2C bus"     # last line ecat prints in each pass


def ping_once(ip_address):
    """True if ip_address answers one ping within 1 s."""
    result = subprocess.run(["ping", "-c", "1", "-W", "1", str(ip_address)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0


def run_ecat_pass(session, ser):
    """
    One pass of ecat_test1b.py on the PTC. It takes the I2C switches out of reset, which the
    WIB's I2C address needs. ecat loops forever, so it is stopped (Ctrl-Z) and killed after one pass.
    """
    WIEC_SERIAL.send_line(ser, ECAT_COMMAND)
    WIEC_SERIAL.read_until_any(ser, [ECAT_PASS_DONE], timeout=session.json_data["wib_ecat_pass_limit_s"])
    ser.write(b"\x1a")
    ser.flush()
    WIEC_SERIAL.run_petalinux_command(ser, "kill -9 %1")


def i2c_ready(session, ser, wib_number):
    """
    A check for wait_until: one ecat pass, then an I2C scan. Returns the scan if the WIB's
    address is in it, else None.
    """
    run_ecat_pass(session, ser)
    output = WIEC_SERIAL.run_petalinux_command(ser, "i2cdetect -y -r 2", timeout=15)
    if expected_wib_i2c_address(session, wib_number) in i2cdetect_addresses(session, output):
        return output
    return None


def read_sensors_when_ready(session, ser, wib_number):
    """
    Waits until the WIB's I2C address shows, switches to its sensors (i2cset), then waits until
    every sensor in wib_expected_sensor_addresses shows. Returns {"i2cset": ..., "i2cdetect": ...}
    for check_slot. If the address never shows, the scan is empty, so check_slot fails it.
    """
    config = session.json_data
    every = config["wib_poll_every_s"]

    found, seconds = WIEC_utils.wait_until(f"WIB {wib_number}: waiting for its I2C address",
                                           lambda: i2c_ready(session, ser, wib_number),
                                           config["wib_i2c_ready_limit_s"], every)
    if found is None:
        session.log(WIEC_utils.fail_text(f"WIB {wib_number}: I2C address did not show within "
                                         f"{config['wib_i2c_ready_limit_s']} s"))
        return {"i2cset": "", "i2cdetect": ""}
    session.log(f"WIB {wib_number}: I2C address showed after {seconds:.0f} s")

    address = expected_wib_i2c_address(session, wib_number)
    set_output = WIEC_SERIAL.run_petalinux_command(ser, f"i2cset -y 2 0x{address} 0x0002")
    expected = set(config["wib_expected_sensor_addresses"])

    def sensors_ready():
        output = WIEC_SERIAL.run_petalinux_command(ser, "i2cdetect -y -r 2", timeout=15)
        if expected <= i2cdetect_addresses(session, output):     # <= on sets: "all of these are in"
            return output
        return None

    output, seconds = WIEC_utils.wait_until(f"WIB {wib_number}: waiting for its sensors",
                                            sensors_ready, config["wib_sensor_ready_limit_s"], every)
    if output is None:
        output = WIEC_SERIAL.run_petalinux_command(ser, "i2cdetect -y -r 2", timeout=15)
        missing = sorted(expected - i2cdetect_addresses(session, output))
        session.log(WIEC_utils.fail_text(f"WIB {wib_number}: sensors missing: {missing}"))
    else:
        session.log(f"WIB {wib_number}: sensors ready after {seconds:.0f} s")
    return {"i2cset": set_output, "i2cdetect": output}



def login_zynq(session):
    """
    Logs into the zynq
    """
    config_data = session.json_data
    try:
        ser = WIEC_SERIAL.login(config_data["wib_petalinux_login_expected_s"])
    except (Exception, SystemExit) as exc:
        session.log(f"Zynq serial login failed ({exc!r}): Check the USB cable and PTC power.")
        return None
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

if __name__ == "__main__":
    print("WIB_setup module is a helper and should be called by the WIEC workflow.")

    






