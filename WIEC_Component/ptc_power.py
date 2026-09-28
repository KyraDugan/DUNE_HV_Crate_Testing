"""WIEC PTC power.

Brings the PL506 48 V up only after every fan is confirmed to be spinning at
the expected frequency, and takes it back down before the fans on the way out.


-------------------------------------------------------------------------------------------------------
config.json keys used:

"WIEC_fan_voltage"                              WIEC PTC fan voltage
"WIEC_fan_current"                              WIEC PTC fan current
"rigol832a_fan_voltage"                         Rigol 832A fan voltage setting, copied from WIEC_fan_voltage
"rigol832a_fan_current"                         Rigol 832A fan current setting, copied from WIEC_fan_current
"keysight970a_fan_num"                          Number of fans
"fan_wait"                                      Time to wait for fans to spin up
"fan_osc_freq"                                  Minimum fan oscillation frequency, Hz
"PL506_channel"                                 PL506 channel to turn on to power the PTC
"PL506_voltage_max"                             PL506 maximum voltage for safe turn-on, V 
"PL506_sense_voltage"                           PL506 expected readback voltage at turn-on, V
"PL506_current_max"                             PL506 maximum current for safe turn-on, A
"PL506_current_limit"                           PL506 current limit, A
"PL506_settle_seconds"                          PL506 delay after enabling channel, s
-------------------------------------------------------------------------------------------------------
"""

import sys
import time
import pyvisa
import wiec_serial as WIEC_SERIAL
from . import cli
from . import WIEC_utils


NAME = "PTC Power"
KEY = "ptc_power"
DESCRIPTION = "Fan-interlocked PL506 48 V power up for the WIEC PTC"
EST_MINUTES = 1


def ensure_fans_on(session):
    """
    Function to check that fans oscillate at the correct speed within the WIEC
    """

    json_data = session.json_data
    session.emergency_shutoff()

    # The WIEC fan settings are not the same as the crate test's
    json_data["rigol832a_fan_voltage"] = json_data["WIEC_fan_voltage"]
    json_data["rigol832a_fan_current"] = json_data["WIEC_fan_current"]
    session.rigol0.setup_fan()
    session.rigol0.power("ON", "fan")
    WIEC_utils.countdown_sleep(session, "Fan on spinup time", json_data['fan_wait'])
    session.rigol1.setup_fanread()
    session.rigol1.power("ON", "fanread")

    try:
        session.keysight.initialize_fan()
        fan_read_signal = session.keysight.measure_fan()

    # Issue arises where the keysight does not clear partial previous run. This attempts to fix.
    except pyvisa.errors.VisaIOError as exc:
        session.log(f"DAQ970A fan read failed ({exc}); reconnecting and retrying one time.")
        try:
            session._keysight.keysight.clear()
            session._keysight.keysight.close()
        except Exception:
            pass
        session._keysight = None
        time.sleep(2)
        session.keysight.initialize_fan()
        fan_read_signal = session.keysight.measure_fan()

    num_fans = int(json_data["keysight970a_fan_num"])
    for fan in range(1, num_fans + 1):
        if fan_read_signal[fan] < json_data["fan_osc_freq"]:
            session.log(
                f"CONFIG ERROR! Fan #{fan} is not oscillating at the correct "
                f"frequency: {fan_read_signal[fan]} Hz"
            )
            session.log("Please check fan connections and restart test")
            session.emergency_shutoff()
            return False

    return True


def turn_on_pl506(session):
    """
    Initializes turn on for PL506, which powers the PTC
    """
    json_data = session.json_data

    readback = session.pl506.safe_turn_on_channel(
        channel=json_data["PL506_channel"],
        max_voltage_v=json_data["PL506_voltage_max"],
        voltage_v=json_data["PL506_sense_voltage"],
        max_current_a=json_data["PL506_current_max"],
        current_limit_a=json_data["PL506_current_limit"],
        settle_s=json_data["PL506_settle_seconds"],
    )
    return readback
   

def power_up(session):
    """
    Powers up the PTC
    """

    json_data = session.json_data

    if not ensure_fans_on(session):
        return False

    WIEC_SERIAL.store_current_usb_ports()

    for tries in range(3):
        readback = turn_on_pl506(session)
        if readback.get("measured_current_a", 0.0) == 0.0 or readback.get("terminal_voltage_v", 0.0) == 0.0:
            session.log(f"Attempt {tries + 1}/3 to turn on PL506 channel {json_data['PL506_channel']} FAILED. Reattempting.")
        else:
            session.log(f"Attempt {tries + 1}/3 to turn on PL506 channel {json_data['PL506_channel']} SUCCESS!")
            break
    else:
        session.log(f"Third and final attempt to turn on PL506 channel {json_data['PL506_channel']} FAILED. Terminating testing.")
        power_down(session)
        return False


    

    #The following line helps with debugging if the pl506 is having trouble booting up. The user does not need to see this normally.
    #session.log(f"PL506 channel {json_data['PL506_channel']} turned on with readback: {readback}")

    session.datastore["pl506_readback"] = readback
    return True


def power_down(session):
    """
    Powers down the PTC
    """
    json_data = session.json_data
    session.pl506.channel_off(json_data["PL506_channel"])
    session.pl506.main_off()
    session.log(f"PL506 channel {json_data['PL506_channel']} turned off")
    session.rigol0.power("OFF", "fan")
    session.rigol1.power("OFF", "fanread")
    return True


def run(session):
    return power_up(session)


if __name__ == "__main__":
    sys.exit(cli.run_single_item(sys.modules[__name__]))
