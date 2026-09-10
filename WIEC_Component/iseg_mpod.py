"""SNMP control of the WIENER MPOD crate and its iseg HV modules.

Focused on what the hv_module_test item needs: discover the module in a slot,
read its serial number and safety loop, ramp its channels together and watch
them for trips.

Three things about this controller are worth knowing before reading the code,
because none of them are what the MIB text suggests:

    moduleSerialNumber            not implemented -- the serial number has to
                                  be parsed out of moduleDescription
    moduleRampSpeedVoltage        a PERCENT of the module's full scale, not
                                  V/s (10 V/s is 0.5% on a 2 kV module)
    moduleHardwareLimitVoltage    also a percent -- the potentiometer setting
    outputHardwareLimit*          per-channel versions are not implemented

Polarity is read from the sign of outputConfigMaxSenseVoltage rather than being
configured -- the slot 2 module is negative.  Setpoints are still written as
positive magnitudes; only the readbacks are signed.
"""

import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

MAX_SNMP_RETRIES = 3

# Errors that will never come right by trying again: the OID does not exist,
# is not writable, or the value was wrong.  A channel of an unpowered module
# reports noAccess, and retrying eight of those with a sleep in between turned
# a doomed call into a 25 second silence.
PERMANENT_SNMP_ERRORS = (
    "noaccess", "nosuchname", "nosuchobject", "nosuchinstance",
    "notwritable", "wrongtype", "wrongvalue", "wronglength",
    "inconsistentvalue", "authorizationerror",
)

# A channel is treated as back at zero below this.
ZERO_VOLTAGE_TOLERANCE_V = 5.0

# outputStatus bit positions, from WIENER-CRATE-MIB.
OUTPUT_STATUS_BITS = {
    0: "outputOn",
    1: "outputInhibit",
    2: "outputFailureMinSenseVoltage",
    3: "outputFailureMaxSenseVoltage",
    4: "outputFailureMaxTerminalVoltage",
    5: "outputFailureMaxCurrent",
    6: "outputFailureMaxTemperature",
    7: "outputFailureMaxPower",
    9: "outputFailureTimeout",
    10: "outputCurrentLimited",
    11: "outputRampUp",
    12: "outputRampDown",
    13: "outputEnableKill",
    14: "outputEmergencyOff",
    15: "outputAdjusting",
    16: "outputConstantVoltage",
    17: "outputLowCurrentRange",
    18: "outputCurrentBoundsExceeded",
    19: "outputFailureCurrentLimit",
    20: "outputCurrentIncreasing",
    21: "outputCurrentDecreasing",
    22: "outputConstantPower",
    23: "outputVoltageRampSpeedLimited",
    24: "outputVoltageBottomReached",
    25: "outputInitCrcCheckBad",
}

# Of those, the ones that must abort a test.  outputAdjusting, outputRampUp,
# outputConstantVoltage and friends are normal states, not faults.
OUTPUT_TRIP_BITS = {1, 2, 3, 4, 5, 6, 7, 9, 14, 19, 25}

OUTPUT_ON_BIT = 0
OUTPUT_RAMP_UP_BIT = 11
OUTPUT_RAMP_DOWN_BIT = 12

# moduleStatus bits, from WIENER-CRATE-MIB.
MODULE_SAFETY_LOOP_GOOD_BIT = 10
MODULE_IS_GOOD_BIT = 12
MODULE_SUPPLY_GOOD_BIT = 13
MODULE_TEMPERATURE_GOOD_BIT = 14
MODULE_NO_RAMP_BIT = 9
MODULE_NO_SUM_ERROR_BIT = 8

# sysStatus bits, from WIENER-CRATE-MIB.
SYS_MAIN_ON_BIT = 0
SYS_INPUT_FAILURE_BIT = 3


class SNMPError(RuntimeError):
    pass


class TripError(RuntimeError):
    """A channel tripped, or reported a fault, while under high voltage."""


@dataclass
class SNMPConfig:
    ip: str
    mib_name: str = "+WIENER-CRATE-MIB"
    read_community: str = "public"
    write_community: str = "guru"
    precision: str = ".9"
    version: str = "2c"
    timeout_s: int = 20
    max_retries: int = MAX_SNMP_RETRIES
    log_commands: bool = False


@dataclass
class ModuleInfo:
    module_id: str          # "ma2"
    slot: int               # 2
    vendor: str             # "iseg"
    model: str              # "E08F2"
    channel_count: int      # 8
    serial_number: str      # "7200489"
    firmware: str           # "07.52"
    description: str
    channels: List[str] = field(default_factory=list)   # ["u200", ...]

    @property
    def is_iseg(self):
        return "iseg" in self.vendor.lower()


class IsegMPOD:
    """snmpget/snmpset/snmpwalk wrapper for one MPOD crate."""

    def __init__(self, cfg: SNMPConfig, logger=None):
        self.cfg = cfg
        self._log = logger or (lambda message: None)

    # ------------------------------------------------------------------
    # Plumbing
    # ------------------------------------------------------------------
    def _run(self, args: List[str], retries: Optional[int] = None) -> str:
        if retries is None:
            retries = max(0, self.cfg.max_retries)

        if self.cfg.log_commands:
            self._log("SNMP: " + " ".join(shlex.quote(a) for a in args))

        last_error = ""
        for attempt in range(retries + 1):
            try:
                result = subprocess.run(
                    args,
                    capture_output=True,
                    text=True,
                    timeout=self.cfg.timeout_s,
                )
            except subprocess.TimeoutExpired:
                last_error = f"timed out after {self.cfg.timeout_s} s"
            else:
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout
                last_error = (result.stderr or result.stdout or "").strip()

            lowered = last_error.lower()
            if any(marker in lowered for marker in PERMANENT_SNMP_ERRORS):
                break

            if attempt < retries:
                time.sleep(1.0)

        raise SNMPError(f"{args[0]} {args[-1] if args else ''} failed: {last_error}")

    def _args(self, command: str, community: str) -> List[str]:
        return [
            command,
            "-Op", self.cfg.precision,
            "-v", self.cfg.version,
            "-m", self.cfg.mib_name,
            "-c", community,
            self.cfg.ip,
        ]

    @staticmethod
    def _value_of(raw: str) -> str:
        """'WIENER-CRATE-MIB::outputSwitch.u200 = INTEGER: off(0)' -> the rhs."""
        for line in raw.splitlines():
            match = re.match(r"^\S+::[^\s=]+\s*=\s*(.+)$", line.strip())
            if match:
                return match.group(1).strip()
        return raw.strip()

    @staticmethod
    def _as_string(value: str) -> str:
        """Drops the SNMP type prefix: 'STRING: \"iseg, E08F2\"' -> 'iseg, E08F2'."""
        text = re.sub(r"^(?:Opaque:\s*)?[A-Za-z0-9]+:\s*", "", value.strip())
        return text.strip().strip('"')

    @staticmethod
    def _as_float(value: str) -> float:
        """Pulls the number out of 'Opaque: Float: -1000.000000000 V'."""
        match = re.search(r"(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)", value)
        if not match:
            raise SNMPError(f"Could not read a number out of {value!r}")
        return float(match.group(1))

    @staticmethod
    def _as_bits(value: str) -> set:
        """'BITS: 00 EE moduleIsNoSumError(8) ...' -> {8, 9, 10, ...}."""
        return {int(n) for n in re.findall(r"\((\d+)\)", value)}

    # ------------------------------------------------------------------
    # Primitives
    # ------------------------------------------------------------------
    def get(self, oid: str) -> str:
        return self._value_of(self._run(self._args("snmpget", self.cfg.read_community) + [oid]))

    def get_float(self, oid: str) -> float:
        return self._as_float(self.get(oid))

    def get_bits(self, oid: str) -> set:
        return self._as_bits(self.get(oid))

    def set_int(self, oid: str, value: int) -> str:
        return self._value_of(
            self._run(self._args("snmpset", self.cfg.write_community) + [oid, "i", str(value)])
        )

    def set_float(self, oid: str, value: float) -> str:
        return self._value_of(
            self._run(self._args("snmpset", self.cfg.write_community) + [oid, "F", str(value)])
        )

    def walk(self, subtree: str) -> Dict[str, str]:
        raw = self._run(self._args("snmpwalk", self.cfg.read_community) + [subtree])
        snapshot = {}
        for line in raw.splitlines():
            match = re.match(r"^\S+::([^\s=]+)\s*=\s*(.+)$", line.strip())
            if match:
                snapshot[match.group(1)] = match.group(2).strip()
        return snapshot

    # ------------------------------------------------------------------
    # Crate
    # ------------------------------------------------------------------
    def is_alive(self, timeout_s: float = 3.0) -> bool:
        """One quick, un-retried poke.  Used to watch the crate go up or down."""
        saved_timeout, saved_retries = self.cfg.timeout_s, self.cfg.max_retries
        self.cfg.timeout_s, self.cfg.max_retries = int(max(1, timeout_s)), 0
        try:
            self.get("sysMainSwitch.0")
            return True
        except SNMPError:
            return False
        finally:
            self.cfg.timeout_s, self.cfg.max_retries = saved_timeout, saved_retries

    def wait_until_alive(self, timeout_s: float = 180.0, poll_s: float = 3.0) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.is_alive():
                return True
            time.sleep(poll_s)
        return False

    def wait_until_dead(self, timeout_s: float = 180.0, poll_s: float = 3.0) -> bool:
        """Confirms the crate really lost power -- the operator may have forgotten."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if not self.is_alive():
                return True
            time.sleep(poll_s)
        return False

    def main_switch(self) -> bool:
        return "on" in self.get("sysMainSwitch.0").lower()

    def turn_on_crate(self):
        return self.set_int("sysMainSwitch.0", 1)

    def turn_off_crate(self):
        return self.set_int("sysMainSwitch.0", 0)

    def get_string(self, oid: str) -> str:
        return self._as_string(self.get(oid))

    def module_count(self) -> int:
        """Modules the controller can currently see.

        Drops to 0 when the module bay loses power, which is what makes it a
        usable proof that a module can be pulled out safely -- see
        module_bay_powered().
        """
        try:
            return int(self._as_float(self.get("moduleNumber.0")))
        except SNMPError:
            return -1

    def output_count(self) -> int:
        try:
            return int(self._as_float(self.get("outputNumber.0")))
        except SNMPError:
            return -1

    def sys_status(self) -> set:
        return self.get_bits("sysStatus.0")

    def module_bay_powered(self) -> Tuple[bool, str]:
        """Is there power on the module backplane?

        Written for a crate whose controller keeps running on standby after the
        supply is switched off: waiting for SNMP to go quiet would wait for
        ever, so the evidence has to be positive instead.  With the bay
        unpowered the controller enumerates no modules and no outputs, and
        sysStatus reports inputFailure.
        """
        modules = self.module_count()
        outputs = self.output_count()
        main_on = self.main_switch()
        status = self.sys_status()
        input_failure = SYS_INPUT_FAILURE_BIT in status

        if modules == 0 and outputs == 0 and not main_on:
            reason = (
                f"controller still on standby, but the module bay is dead: "
                f"{modules} modules, {outputs} outputs, sysMainSwitch off"
                + (", sysStatus reports inputFailure" if input_failure else "")
            )
            return False, reason

        return True, (
            f"sysMainSwitch is {'on' if main_on else 'off'}, "
            f"{modules} modules and {outputs} outputs still enumerated"
        )

    def crate_serial(self) -> str:
        try:
            return self.get_string("psSerialNumber.0")
        except SNMPError:
            return ""

    # ------------------------------------------------------------------
    # Modules
    # ------------------------------------------------------------------
    @staticmethod
    def parse_description(module_id: str, description: str) -> ModuleInfo:
        """'iseg, E08F2, 08, 7200489, 07.52' -> ModuleInfo."""
        text = IsegMPOD._as_string(description)
        fields = [f.strip() for f in text.split(",")]
        fields += [""] * (5 - len(fields))
        slot = int(re.sub(r"\D", "", module_id) or 0)

        try:
            channel_count = int(fields[2])
        except ValueError:
            channel_count = 8

        return ModuleInfo(
            module_id=module_id,
            slot=slot,
            vendor=fields[0],
            model=fields[1],
            channel_count=channel_count,
            serial_number=fields[3],
            firmware=fields[4],
            description=text,
            channels=[f"u{slot * 100 + i}" for i in range(channel_count)],
        )

    def module_in_slot(self, slot: int) -> ModuleInfo:
        module_id = f"ma{slot}"
        description = self.get_string(f"moduleDescription.{module_id}")
        if "no such" in description.lower():
            raise SNMPError(f"No module reported in slot {slot} ({module_id})")
        return self.parse_description(module_id, description)

    def module_status(self, module_id: str) -> set:
        """The module status bits, or an error if the module is not there.

        A missing module answers "No Such Instance", which contains no bit
        patterns at all -- so parsing it as bits yields an empty set, and
        every "is bit N set?" question quietly answers "no".  That reads as a
        perfectly plausible module state (safety loop open, module not good)
        when the truth is that nothing was measured.
        """
        raw = self.get(f"moduleStatus.{module_id}")
        if "no such" in raw.lower():
            raise SNMPError(
                f"{module_id} is not enumerated -- the module bay is powered "
                f"down or the slot is empty ({raw})"
            )
        return self._as_bits(raw)

    def safety_loop_closed(self, module_id: str) -> bool:
        """True when the safety jumper is in place.

        moduleStatus bit 10, 'Safety loop is closed'.  This follows the
        **jumper only** -- measured on ma2: with the test kit removed and the
        jumper still in, the bit stays set.  It is NOT the green LED; see
        module_good().
        """
        return MODULE_SAFETY_LOOP_GOOD_BIT in self.module_status(module_id)

    def module_good(self, module_id: str):
        """The module's overall state -- what the green LED shows.

        Measured on ma2, same module, same OID:

            test kit + jumper : bits 8, 10, 12 set
            jumper only       : bit 10 set, bits 8 and 12 CLEAR, LED off

        So removing the test kit clears moduleIsGood(12) and
        moduleIsNoSumError(8) while leaving the safety loop alone.  Returns
        (good, detail).
        """
        bits = self.module_status(module_id)
        good = MODULE_IS_GOOD_BIT in bits
        no_sum_error = MODULE_NO_SUM_ERROR_BIT in bits
        detail = (
            f"moduleIsGood={'set' if good else 'clear'}, "
            f"moduleIsNoSumError={'set' if no_sum_error else 'clear'}"
        )
        return (good and no_sum_error), detail

    def module_health(self, module_id: str) -> List[str]:
        """Returns the human-readable problems, empty when the module is happy."""
        bits = self.module_status(module_id)
        problems = []
        if MODULE_IS_GOOD_BIT not in bits:
            problems.append("module state is not good")
        if MODULE_SUPPLY_GOOD_BIT not in bits:
            problems.append("module supply is not good")
        if MODULE_TEMPERATURE_GOOD_BIT not in bits:
            problems.append("module temperature is not good")
        return problems

    def module_is_ramping(self, module_id: str) -> bool:
        return MODULE_NO_RAMP_BIT not in self.module_status(module_id)

    def hardware_limit_voltage_percent(self, module_id: str) -> float:
        """The voltage potentiometer, in percent of the module's full scale."""
        return self.get_float(f"moduleHardwareLimitVoltage.{module_id}")

    def hardware_limit_current_percent(self, module_id: str) -> float:
        return self.get_float(f"moduleHardwareLimitCurrent.{module_id}")

    def clear_events(self, module_id: str):
        self.set_int(f"moduleDoClear.{module_id}", 1)

    # ------------------------------------------------------------------
    # Channels
    # ------------------------------------------------------------------
    def nominal_voltage(self, channel: str) -> float:
        """Signed full scale: -2000 V on the negative slot 2 module."""
        return self.get_float(f"outputConfigMaxSenseVoltage.{channel}")

    def nominal_current(self, channel: str) -> float:
        return self.get_float(f"outputConfigMaxCurrent.{channel}")

    def polarity(self, channel: str) -> int:
        """+1 or -1, taken from the sign of the module's full scale."""
        return -1 if self.nominal_voltage(channel) < 0 else 1

    def measured_voltage(self, channel: str) -> float:
        return self.get_float(f"outputMeasurementSenseVoltage.{channel}")

    def measured_current(self, channel: str) -> float:
        return self.get_float(f"outputMeasurementCurrent.{channel}")

    def channel_status(self, channel: str) -> set:
        return self.get_bits(f"outputStatus.{channel}")

    def channel_faults(self, channel: str) -> List[str]:
        """Trip-worthy faults only; 'adjusting' and friends are not faults."""
        bits = self.channel_status(channel)
        return [OUTPUT_STATUS_BITS.get(bit, f"bit{bit}") for bit in sorted(bits & OUTPUT_TRIP_BITS)]

    def channel_is_ramping(self, channel: str) -> bool:
        bits = self.channel_status(channel)
        return bool(bits & {OUTPUT_RAMP_UP_BIT, OUTPUT_RAMP_DOWN_BIT})

    def set_voltage(self, channel: str, voltage: float):
        """Setpoints are written as magnitudes.

        The slot 2 module is negative polarity, yet outputVoltage reads back
        positive and outputConfigMaxSenseVoltage reads -2000 V.  Writing the
        magnitude is what the working standalone QC scripts do; the module
        applies its own polarity.
        """
        return self.set_float(f"outputVoltage.{channel}", abs(voltage))

    def get_setpoint(self, channel: str) -> float:
        return abs(self.get_float(f"outputVoltage.{channel}"))

    def set_current(self, channel: str, current: float):
        return self.set_float(f"outputCurrent.{channel}", current)

    def channel_on(self, channel: str):
        return self.set_int(f"outputSwitch.{channel}", 1)

    def channel_off(self, channel: str):
        return self.set_int(f"outputSwitch.{channel}", 0)

    def clear_channel_events(self, channel: str):
        """outputSwitch = clearEvents(10): required before a channel that has
        tripped will ramp up again."""
        return self.set_int(f"outputSwitch.{channel}", 10)

    def set_ramp_rate(self, module_id: str, rate_v_per_s: float,
                      nominal_voltage_v: float) -> float:
        """Sets the ramp speed of a whole iseg module, in V/s.

        The ramp speed of an iseg module is one **module-level percentage** of
        full scale, shared by all eight channels -- the MIB says so outright:
        "iseg HV modules have the same ramp speed value for all channels".

        Writing the per-channel outputVoltageRiseRate instead looks like it
        works -- snmpset returns success -- but the value never changes: a
        commanded 10 V/s still read back as 100 V/s, which is exactly the 5%
        the module was already set to.  Always write the percentage, and always
        verify with get_ramp_rate().
        """
        full_scale = abs(nominal_voltage_v)
        if full_scale <= 0:
            raise SNMPError(f"{module_id}: nominal voltage reads 0, cannot set a ramp rate")
        percent = 100.0 * abs(rate_v_per_s) / full_scale
        self.set_float(f"moduleRampSpeedVoltage.{module_id}", percent)
        return percent

    def get_ramp_rate(self, channel: str) -> float:
        """The channel rise rate in V/s, as a magnitude."""
        return abs(self.get_float(f"outputVoltageRiseRate.{channel}"))

    def get_module_ramp_percent(self, module_id: str) -> float:
        """The module-wide ramp speed, in percent of full scale.

        10 V/s is 0.5% on a 2 kV module.
        """
        return abs(self.get_float(f"moduleRampSpeedVoltage.{module_id}"))

    def module_present(self, slot: int) -> bool:
        """Is the module in this slot enumerated yet?

        The bay enumerates progressively after power-up, so "moduleNumber > 0"
        is not enough -- the slot under test can still be missing.
        """
        try:
            description = self.get_string(f"moduleDescription.ma{slot}")
        except SNMPError:
            return False
        return "no such" not in description.lower() and bool(description.strip())

    # ------------------------------------------------------------------
    # Group operations
    # ------------------------------------------------------------------
    def read_all(self, channels: List[str]) -> Dict[str, Dict[str, object]]:
        """One sample of every channel: V, I and the fault list.

        Three snmpwalks rather than three snmpgets per channel: each snmpget is
        a separate process, so walking is roughly 5x faster for 8 channels and
        keeps the samples during a ramp closely spaced in time.
        """
        voltages = self.walk("outputMeasurementSenseVoltage")
        currents = self.walk("outputMeasurementCurrent")
        statuses = self.walk("outputStatus")

        sample = {}
        for channel in channels:
            status_bits = self._as_bits(statuses.get(f"outputStatus.{channel}", ""))
            sample[channel] = {
                "voltage": self._as_float(
                    voltages.get(f"outputMeasurementSenseVoltage.{channel}", "nan")
                ),
                "current": self._as_float(
                    currents.get(f"outputMeasurementCurrent.{channel}", "nan")
                ),
                "faults": [
                    OUTPUT_STATUS_BITS.get(bit, f"bit{bit}")
                    for bit in sorted(status_bits & OUTPUT_TRIP_BITS)
                ],
                "ramping": bool(status_bits & {OUTPUT_RAMP_UP_BIT, OUTPUT_RAMP_DOWN_BIT}),
                "on": OUTPUT_ON_BIT in status_bits,
            }
        return sample

    def all_off(self, channels: List[str]):
        """Best effort -- one unreachable channel must not stop the others."""
        failures = []
        for channel in channels:
            try:
                self.channel_off(channel)
            except SNMPError as exc:
                failures.append(f"{channel}: {exc}")
        return failures

    def wait_until_zero(self, channels: List[str], timeout_s: float = 300.0,
                        poll_s: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                voltages = [abs(self.measured_voltage(c)) for c in channels]
            except SNMPError:
                time.sleep(poll_s)
                continue
            if all(v < ZERO_VOLTAGE_TOLERANCE_V for v in voltages):
                return True
            time.sleep(poll_s)
        return False
