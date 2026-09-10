"""Shared state for the WIEC_Component test items.

A TestSession owns the parsed config, the instrument handles, the results
directory, the datastore that becomes the output JSON, and the spreadsheet row
this run writes into.  Instruments are opened lazily so that importing this
module -- listing items, printing suggestions, running preflight -- works fine
on a machine with no hardware attached.
"""

import copy
import json
import os
import time
import traceback
from datetime import datetime

import pyvisa

from keysight_daq970a import Keysight970A
from rigol_dp832a import RigolDP832A
from caen_r8033dm_wrapper import CAENR8033DM_WRAPPER
from pl506 import PL506

from .iseg_mpod import IsegMPOD, SNMPConfig
from .spreadsheet import ResultSpreadsheet


class TestSession:
    def __init__(self, config_file, test_name, prefix="WIEC Component"):
        self.prefix = prefix
        self.config_file = config_file

        with open(config_file, "r") as jsonfile:
            self.json_data = json.load(jsonfile)

        self.test_name = test_name
        self.rounding_factor = self.json_data["rounding_factor"]
        self.start_time = datetime.now()

        self._rm = None
        self._caen = None
        self._keysight = None
        self._rigol0 = None
        self._rigol1 = None
        self._pl506 = None
        self._mpod = None

        # A snapshot, not a reference: fan_pwm_sweep rewrites
        # rigol832a_fan_voltage while it sweeps, and the archived config must
        # keep showing the nominal value the run actually started from.
        self.datastore = {
            "input_params": copy.deepcopy(self.json_data),
            "test_name": test_name,
            "start_time": self.start_time,
            "Tests": {},
        }
        self.results = {}

        self._make_results_dir()
        self.sheet = ResultSpreadsheet(self.json_data, prefix=prefix)
        self.row = self.sheet.open(test_name, self.output_path)
        self.sheet.write_stamp(
            datetime.today().strftime("%m/%d/%Y"),
            datetime.today().strftime("%I:%M:%S %p"),
        )
        self.datastore["spreadsheet_path"] = self.sheet.path

    def log(self, message):
        print(f"{self.prefix} --> {message}")

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------
    def _make_results_dir(self):
        if self.json_data["relative"] == "True":
            self.output_path = os.path.abspath(self.json_data["output_directory"])
        else:
            self.output_path = os.path.normpath(self.json_data["output_directory"])

        stamp = datetime.today().strftime("%Y%m%d%H%M%S")
        self.results_path = os.path.join(self.output_path, stamp)
        os.makedirs(self.results_path, exist_ok=True)

        self.json_output_file = os.path.join(
            self.results_path, f"{stamp}_{self.test_name}.json"
        )
        self.datastore["json_path"] = self.json_output_file

    # ------------------------------------------------------------------
    # Instruments -- opened on first use
    # ------------------------------------------------------------------
    @property
    def rm(self):
        if self._rm is None:
            self._rm = pyvisa.ResourceManager("@py")
        return self._rm

    @property
    def caen(self):
        if self._caen is None:
            self._caen = CAENR8033DM_WRAPPER(self.json_data)
        return self._caen

    @property
    def keysight(self):
        if self._keysight is None:
            self._keysight = Keysight970A(self.rm, self.json_data)
        return self._keysight

    @property
    def rigol0(self):
        if self._rigol0 is None:
            rigol = RigolDP832A(self.rm, self.json_data, 0)
            rigol.setup_fan()
            rigol.setup_heater_supply()
            rigol.setup_heater_switch()
            self._rigol0 = rigol
        return self._rigol0

    @property
    def rigol1(self):
        if self._rigol1 is None:
            rigol = RigolDP832A(self.rm, self.json_data, 1)
            rigol.setup_hvpullup()
            rigol.setup_hvpullup2()
            rigol.setup_fanread()
            self._rigol1 = rigol
        return self._rigol1

    @property
    def pl506(self):
        if self._pl506 is None:
            self._pl506 = PL506(ip=self.json_data["PL506_IP_ADDR"])
        return self._pl506

    @property
    def mpod(self):
        """The WIENER MPOD crate, over SNMP.

        Only hv_module_test uses it.  Nothing is opened here -- snmpget runs as
        a subprocess -- so touching this property cannot fail on a machine with
        no crate attached.
        """
        if self._mpod is None:
            self._mpod = IsegMPOD(
                SNMPConfig(
                    ip=self.json_data.get("iseg_ip", "169.254.4.31"),
                    read_community=self.json_data.get("iseg_read_community", "public"),
                    write_community=self.json_data.get("iseg_write_community", "guru"),
                ),
                logger=self.log,
            )
        return self._mpod

    def connect_all(self):
        """Open every instrument up front.

        Worth doing before a long unattended run so a missing instrument fails
        in the first few seconds instead of an hour in.
        """
        self.log("Connecting to all instruments...")
        self.caen
        self.keysight
        self.rigol0
        self.rigol1
        self.pl506
        self.log("All instruments connected")

    def reset_pyvisa_connections(self):
        """Reopen the VISA instruments after a dropped TCP connection."""
        try:
            self._rigol0.rigol.close()
        except Exception:
            pass
        self._rigol0 = None

        try:
            self._rigol1.rigol.close()
        except Exception:
            pass
        self._rigol1 = None

        try:
            self._keysight.keysight.close()
        except Exception:
            pass
        self._keysight = None

        # Touch them so the reconnect (and its channel setup) happens now.
        self.rigol0
        self.rigol1
        self.keysight

    # ------------------------------------------------------------------
    # Safety
    # ------------------------------------------------------------------
    def emergency_shutoff(self):
        """Power everything down, in the safe order.

        Every step is guarded on its own: a failure turning off the CAEN must
        not stop us from turning off the heater, the PTC supply and the fans.
        """
        self.log("Powering down all outputs...")
        failures = []

        def step(description, action):
            try:
                action()
            except BaseException:
                failures.append(description)
                print(f"{self.prefix} --> WARNING: could not {description}")
                print(traceback.format_exc())

        if self._caen is not None:
            step("turn off the CAEN HV channels",
                 lambda: self._caen.turn_off(list(range(16)), emergency=True))

        # Only the channels of the slot under test: the MPOD may be powering
        # other things in the lab, so the crate main switch is left alone here.
        # hv_module_test switches the whole crate off itself when it aborts.
        if self._mpod is not None:
            slot = int(self.json_data.get("iseg_slot", 2))
            channels = [f"u{slot * 100 + i}" for i in range(8)]
            step(f"turn off the MPOD slot {slot} HV channels",
                 lambda: self._mpod.all_off(channels))

        if self._rigol0 is not None:
            step("turn off the heater supply",
                 lambda: self._rigol0.power("OFF", "heat_supply"))
            step("turn off the heater switch",
                 lambda: self._rigol0.power("OFF", "heat_switch"))

        # PL506 before the fans: the PTC must never sit powered without airflow.
        if self._pl506 is not None:
            step("turn off the PL506 channel",
                 lambda: self._pl506.channel_off(self.json_data["PL506_channel"]))
            step("turn off the PL506 main switch", self._pl506.main_off)

        if self._rigol0 is not None:
            step("turn off the fan supply", lambda: self._rigol0.power("OFF", "fan"))
        if self._rigol1 is not None:
            step("turn off the fan readback supply",
                 lambda: self._rigol1.power("OFF", "fanread"))
            step("turn off the HV pullups",
                 lambda: (self._rigol1.power("OFF", "hvpullup"),
                          self._rigol1.power("OFF", "hvpullup2")))
        if self._keysight is not None:
            step("reset the relays", lambda: self._keysight.set_relay(0, 0))

        if failures:
            print(f"{self.prefix} --> !! {len(failures)} shutdown step(s) FAILED: "
                  f"{', '.join(failures)}")
            print(f"{self.prefix} --> !! CHECK THE HARDWARE BY HAND before leaving")
            return False

        self.log("All outputs off")
        return True

    # ------------------------------------------------------------------
    # Results
    # ------------------------------------------------------------------
    def record(self, key, passed):
        self.results[key] = bool(passed)
        self.datastore.setdefault("Items", {})[key] = "Pass" if passed else "Fail"

    def save_datastore(self):
        end_time = datetime.now()
        self.datastore["end_time"] = end_time
        self.datastore["test_time"] = end_time - self.start_time
        with open(self.json_output_file, "w", encoding="utf-8") as f:
            json.dump(self.datastore, f, ensure_ascii=False, indent=4, default=str)

    def finalize(self):
        """Write the overall verdict, save the JSON, and report where it went."""
        passed = bool(self.results) and all(self.results.values())
        self.datastore["overall"] = "Pass" if passed else "Fail"
        self.sheet.write_overall(self.test_name, passed)
        self.save_datastore()

        self.log(f"Test result: {self.datastore['overall']}")
        self.log(f"Spreadsheet: {self.sheet.path}")
        self.log(f"Results:     {self.results_path}")
        return passed

    def beep_sequence(self):
        # The first beep is always longer for some reason
        try:
            self.rigol0.beep()
            time.sleep(1)
            self.rigol0.beep()
            time.sleep(0.5)
            self.rigol0.beep()
            time.sleep(0.125)
            self.rigol0.beep()
            time.sleep(0.125)
            self.rigol0.beep()
            time.sleep(0.5)
            self.rigol0.beep()
            time.sleep(0.75)
            self.keysight.beep()
            time.sleep(0.5)
            self.keysight.beep()
        except Exception:
            pass


def shutdown_only(config_file, prefix="WIEC Component"):
    """Power everything down without starting a test session.

    Used by the top script's shutdown option and after a crashed run: opens
    each instrument on its own so one unreachable device cannot stop the rest
    from being turned off.
    """
    with open(config_file, "r") as jsonfile:
        json_data = json.load(jsonfile)

    print(f"{prefix} --> Powering down every instrument...")
    failures = []

    def step(description, action):
        try:
            action()
            print(f"{prefix} --> {description}: done")
        except BaseException:
            failures.append(description)
            print(f"{prefix} --> WARNING: {description} FAILED")
            print(traceback.format_exc())

    def caen_off():
        CAENR8033DM_WRAPPER(json_data).turn_off(list(range(16)), emergency=True)

    def rigol0_off():
        rm = pyvisa.ResourceManager("@py")
        rigol = RigolDP832A(rm, json_data, 0)
        rigol.setup_fan()
        rigol.setup_heater_supply()
        rigol.setup_heater_switch()
        rigol.power("OFF", "heat_supply")
        rigol.power("OFF", "heat_switch")
        rigol.power("OFF", "fan")

    def rigol1_off():
        rm = pyvisa.ResourceManager("@py")
        rigol = RigolDP832A(rm, json_data, 1)
        rigol.setup_hvpullup()
        rigol.setup_hvpullup2()
        rigol.setup_fanread()
        rigol.power("OFF", "hvpullup")
        rigol.power("OFF", "hvpullup2")
        rigol.power("OFF", "fanread")

    def pl506_off():
        supply = PL506(ip=json_data["PL506_IP_ADDR"])
        supply.channel_off(json_data["PL506_channel"])
        supply.main_off()

    def relays_off():
        rm = pyvisa.ResourceManager("@py")
        Keysight970A(rm, json_data).set_relay(0, 0)

    def mpod_slot_off():
        """The slot under test only -- the crate may power other things."""
        mpod = IsegMPOD(SNMPConfig(
            ip=json_data.get("iseg_ip", "169.254.4.31"),
            read_community=json_data.get("iseg_read_community", "public"),
            write_community=json_data.get("iseg_write_community", "guru"),
        ))
        if not mpod.is_alive():
            print(f"{prefix} --> MPOD not reachable, nothing to turn off")
            return
        slot = int(json_data.get("iseg_slot", 2))
        for channel in [f"u{slot * 100 + i}" for i in range(8)]:
            mpod.set_voltage(channel, 0.0)
        mpod.all_off([f"u{slot * 100 + i}" for i in range(8)])

    step("turn off the CAEN HV channels", caen_off)
    step("turn off the MPOD HV channels of the slot under test", mpod_slot_off)
    step("turn off the PL506 PTC supply", pl506_off)
    step("turn off the heater and fan supplies (Rigol #0)", rigol0_off)
    step("turn off the pullup and fan readback supplies (Rigol #1)", rigol1_off)
    step("reset the relays", relays_off)

    if failures:
        print(f"{prefix} --> !! {len(failures)} step(s) FAILED: {', '.join(failures)}")
        print(f"{prefix} --> !! CHECK THE HARDWARE BY HAND")
        return False

    print(f"{prefix} --> Everything is off")
    return True
