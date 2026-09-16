"""Heater test.

Measures the resistance of the four heating elements with no power applied,
then powers them and records the RTD temperature rise.

-------------------------------------------------------------------------------------------------------
config.json keys used:

"heating_element_min"                           Minimum acceptable heating element resistance
"heating_element_max"                           Maximum acceptable heating element resistance
'heat_wait'                                     Seconds to wait for the heaters to reach steady state
'temp_increase_min'                             Minimum acceptable temperature increase
'temp_increase_max'                             Maximum acceptable temperature increase
-------------------------------------------------------------------------------------------------------
"""

import sys
import time

from . import cli

NAME = "Heater Test"
KEY = "heater_test"
DESCRIPTION = "Heating element resistance and RTD temperature rise"
EST_MINUTES = 1


def run(session):
    json_data = session.json_data
    sheet = session.sheet
    result = True

    # Resistance first, with nothing connected to the elements
    session.keysight.initialize_resistance()
    heater_resistance = session.keysight.measure_resistance()
    session.log(f"Heating element resistances are {heater_resistance}")

    for i in range(1, 5):
        passed = (
            json_data["heating_element_min"]
            < heater_resistance[i]
            < json_data["heating_element_max"]
        )
        sheet.write(sheet.tc_res_first_col - 1 + i, heater_resistance[i], passed,
                    rounded=True)
        session.datastore["Tests"][f"heating_element_test_{i}"] = (
            "Pass" if passed else "Fail"
        )
        result = result and passed

    session.datastore["heater_resistance"] = heater_resistance

    # Then the temperature rise
    session.keysight.initialize_rtd()
    temp1 = session.keysight.measure_rtd()

    session.rigol0.power("ON", "heat_supply")
    session.rigol0.power("ON", "heat_switch")
    session.log(
        f"Heat turned on, waiting {json_data['heat_wait']} seconds "
        "for the sensors to heat up..."
    )
    try:
        time.sleep(json_data["heat_wait"])
        supply_voltage = session.rigol0.get_voltage("heat_supply")
        supply_current = session.rigol0.get_current("heat_supply")
        switch_voltage = session.rigol0.get_voltage("heat_switch")
        switch_current = session.rigol0.get_current("heat_switch")
        temp2 = session.keysight.measure_rtd()
    finally:
        # The heaters come off even if a measurement raised
        session.rigol0.power("OFF", "heat_supply")
        session.rigol0.power("OFF", "heat_switch")
        session.log("Heat turned off")

    temp_rise = [temp2[i] - temp1[i] for i in range(1, 5)]

    session.log(f"Heat power supply was {supply_voltage}V and {supply_current}A")
    session.log(f"Heat power switch was {switch_voltage}V and {switch_current}A")
    session.log(f"Original temperatures were {temp1}")
    session.log(
        f"Temperatures after {json_data['heat_wait']} seconds were {temp2}, "
        f"a rise of {temp_rise}"
    )

    for i in range(4):
        passed = (
            json_data["temp_increase_min"]
            < temp_rise[i]
            < json_data["temp_increase_max"]
        )
        sheet.write(sheet.tc_res_first_col + 4 + i, temp_rise[i], passed, rounded=True)
        session.datastore["Tests"][f"temperature_rise_test_{i}"] = (
            "Pass" if passed else "Fail"
        )
        result = result and passed

    session.datastore["heater_supply_voltage"] = supply_voltage
    session.datastore["heater_supply_current"] = supply_current
    session.datastore["heater_switch_voltage"] = switch_voltage
    session.datastore["heater_switch_current"] = switch_current
    session.datastore["temp1"] = temp1
    session.datastore["temp2"] = temp2
    session.datastore["temp_rise"] = temp_rise

    sheet.save()
    return result


if __name__ == "__main__":
    sys.exit(cli.run_single_item(sys.modules[__name__]))
