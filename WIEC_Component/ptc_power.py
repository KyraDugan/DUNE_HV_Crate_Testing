"""WIEC PTC power.

Brings the PL506 48 V up only after every fan is confirmed to be spinning at
the expected frequency, and takes it back down before the fans on the way out.
"""

import sys

from . import cli

NAME = "PTC Power"
KEY = "ptc_power"
DESCRIPTION = "Fan-interlocked PL506 48 V power up for the WIEC PTC"
EST_MINUTES = 1


def power_up(session):
    json_data = session.json_data

    session.emergency_shutoff()

    # The WIEC fan settings are not the same as the crate test's
    json_data["rigol832a_fan_voltage"] = json_data["WIEC_fan_voltage"]
    json_data["rigol832a_fan_current"] = json_data["WIEC_fan_current"]
    session.rigol0.setup_fan()
    session.rigol0.power("ON", "fan")
    session.rigol1.setup_fanread()
    session.rigol1.power("ON", "fanread")

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

    readback = session.pl506.safe_turn_on_channel(
        channel=json_data["PL506_channel"],
        max_voltage_v=json_data["PL506_voltage_max"],
        voltage_v=json_data["PL506_sense_voltage"],
        max_current_a=json_data["PL506_current_max"],
        current_limit_a=json_data["PL506_current_limit"],
        settle_s=json_data["PL506_settle_seconds"],
    )

    session.log(
        f"PL506 channel {json_data['PL506_channel']} turned on with readback:"
    )
    print(readback)
    session.datastore["pl506_readback"] = readback
    return True


def power_down(session):
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
