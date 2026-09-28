"""Fan test at nominal voltage.

Powers the fans from Rigol #0 and the readback pullup from Rigol #1, waits for
steady state, then records the supply voltage/current and the RD signal of
every fan.

-------------------------------------------------------------------------------------------------------
config.json keys used:

'rigol832a_fan_voltage'                         Nominal voltage to power the fans
'fan_wait'                                      Seconds to wait for fans to reach steady state 
'fan_voltage_min'                               Minimum acceptable fan supply voltage
'fan_voltage_max'                               Maximum acceptable fan supply voltage
'fan_current_min'                               Minimum acceptable fan supply current
'fan_current_max'                               Maximum acceptable fan supply current
"keysight970a_fan_num"                          Number of fans to test
"fan_read_min"                                  Minimum acceptable fan RD signal (Hz)
"fan_read_max"                                  Maximum acceptable fan RD signal (Hz)
-------------------------------------------------------------------------------------------------------
"""

import sys
import time

from . import cli
from . import WIEC_utils

NAME = "Fan Test"
KEY = "fan_test"
DESCRIPTION = "Fan supply V/I and the 6 RD signals at the nominal fan voltage"
EST_MINUTES = 1


def measure(session):
    """Powers the fans, takes one set of readings, powers them down again.

    Also used by fan_pwm_sweep, which needs the measurement but must not touch
    the nominal spreadsheet row.
    """
    json_data = session.json_data

    session.keysight.initialize_fan()
    session.rigol0.power("ON", "fan")
    session.rigol1.power("ON", "fanread")

    session.log(
        f"Fans tested at the nominal voltage specified ..... "
        f"{json_data['rigol832a_fan_voltage']}"
    )
    
    WIEC_utils.countdown_sleep(session, "Fans on, waiting for steady state", json_data['fan_wait'])

    # Take all data twice, the first set is discarded for good measure
    for _ in range(2):
        readings = {
            "fan_voltage": session.rigol0.get_voltage("fan"),
            "fan_current": session.rigol0.get_current("fan"),
            "fanread_voltage": session.rigol1.get_voltage("fanread"),
            "fanread_current": session.rigol1.get_current("fanread"),
            "fan_read_signal": session.keysight.measure_fan(),
        }

    session.rigol0.power("OFF", "fan")
    session.rigol1.power("OFF", "fanread")
    session.log("Fans turned off")
    session.log(
        f"Fan power supply was {readings['fan_voltage']}V "
        f"and {readings['fan_current']}A"
    )
    session.log(f"Read signal for each fan was {readings['fan_read_signal']}")
    session.log(
        f"Fan read pullup supply was {readings['fanread_voltage']}V "
        f"and {readings['fanread_current']}A"
    )

    session.datastore.update(readings)
    return readings


def run(session):
    json_data = session.json_data
    sheet = session.sheet
    readings = measure(session)

    result = True

    passed = (
        json_data["fan_voltage_min"] < readings["fan_voltage"] < json_data["fan_voltage_max"]
    )
    sheet.write(4, readings["fan_voltage"], passed)
    session.datastore["Tests"]["fan_voltage_test"] = "Pass" if passed else "Fail"
    result = result and passed

    passed = (
        json_data["fan_current_min"] < readings["fan_current"] < json_data["fan_current_max"]
    )
    sheet.write(5, readings["fan_current"], passed)
    session.datastore["Tests"]["fan_current_test"] = "Pass" if passed else "Fail"
    result = result and passed

    num_fans = int(json_data["keysight970a_fan_num"])
    for i in range(1, num_fans + 1):
        signal = readings["fan_read_signal"][i]
        passed = json_data["fan_read_min"] < signal < json_data["fan_read_max"]
        sheet.write(sheet.fan_rd_first_col - 1 + i, signal, passed, rounded=True)
        # Recorded on both branches -- a missing key used to be the only sign
        # of a failed fan in the JSON.
        session.datastore["Tests"][f"fan_signal_test_{i}"] = "Pass" if passed else "Fail"
        result = result and passed

    sheet.save()
    return result


if __name__ == "__main__":
    sys.exit(cli.run_single_item(sys.modules[__name__]))
