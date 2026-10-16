"""
WIEC FEMB pass-through checkout (WIB side)

Version of femb_assembly_chk.py for the WIEC crate test. Every warm
test of the original can run, and each one is switched on or off in
config.json ("wiec_femb_tests"). They run in the order originally 
given in the CTS script:

    power                 2.1  Initial power: V, I, P of each rail
    registers             2.2  COLDATA and ColdADC default registers
    noise                 3.1  Pedestal and RMS noise, SE 200 mV, 14 mV/fC, 2 us      (ADC data)
    noise_peaking_times   3.1  RMS at 0.5/1/2/3 us, SE 900 mV (BNL's optional "sp")  (ADC data)
    se_power              3.2  Power in the SE configuration
    se_rails              3.3  FEMB LDO voltages, SE configuration
    se_pulse              3.4  SE pulse response, 900 mV                             (ADC data)
    diff_pulse            4.1  DIFF pulse response, 900 mV                           (ADC data)
    diff_power            4.2  Power in the DIFF configuration
    diff_rails            4.3  FEMB LDO voltages, DIFF configuration
    monitor_path          5    FE temperature, bandgap, ColdADC references per ASIC


Always run, whatever the selection: FEMB power on, the connection checks,
the safety current cut (it protects the FEMBs), and FEMB power off at the end.
A FEMB position is dropped (switched off, not tested further, reported with
the reason) when, in this order:
    - it draws no current right after power on: FEMB or power cable not connected
    - it does not answer on I2C within "i2c_tries" retries: data cable problem
    - its current is outside the safety limits (BNL Part 2.1)
so a missing FEMB costs seconds, not BNL's 100 I2C retries. The monitor path
subtracts each FEMB's ground, which the SE rail monitor measures, so the SE
rails are read whenever monitor_path is on (and reported only if se_rails is).

Each test sets up its own FEMB configuration first, so its result does not
depend on which other tests ran. The ADC data tests use BNL's own analysis
(components.assembly_function); they report BNL's verdict and summary numbers,
and BNL's plots stay in CHK/Report/ for femb_checkout.py to copy back.

This script only MEASURES. Pass/fail is decided on the PC by
femb_checkout.grade() using wiec_femb_limits from config.json.

The WIB cannot read config.json, so femb_checkout.py sends the settings as one
JSON object on stdin:
    {"tests": {"power": true, ...},                          which tests run
     "femb_ids": {"0": "FEMB_0", ...},                       serial per position (BNL report names)
     "dc2dc_V": {"LArASIC": 3.0, "ColdDATA": 3.0, "ColdADC": 3.5},
     "safety_A": {"presence_min_A": 0.05, "BIAS_abs_max": 0.15, "LArASIC_min": 0.3, "ColdDATA_max": 0.3},
     "monitor_samples": 10,                                  monitor ADC readings averaged
     "monitor_lsb_mV": 0.152587890625,                       mV per monitor ADC count
     "data_samples": 5,                                      spy buffer captures per ADC data test
     "settle_s": 3.5,                                        wait after configuring, before capture
     "toy_tpc": "PCB_Based",                                 label used in BNL's report folder names
     "i2c_tries": 10}                                        I2C configuration retries before a FEMB is dropped (BNL: 100)

The master copy lives in the DUNE-HV-Crate-Testing repo. femb_checkout.py
copies it into BNL_CE_WIB_SW_QC/ on the WIB before every run, so do not edit
the copy on the WIB.

Usage (on the WIB, inside BNL_CE_WIB_SW_QC; normally femb_checkout.py does this):
    python3 wiec_femb_chk.py 0 1 2 3 < settings.json     FEMB positions to test (0-3)

The last line printed on success is:
    WIEC_RESULT_JSON {...}

"""

import sys
import time
import json
import signal
from wib_cfgs import WIB_CFGS

RESULT_MARKER = "WIEC_RESULT_JSON"     # protocol with femb_checkout.py: must match RESULT_MARKER there
FEMB_POSITIONS = 4                     # FEMB connectors on a WIB: positions are 0-3

# Must match TEST_NAMES in femb_checkout.py. The order is the order they run in.
TEST_NAMES = ["power", "registers", "noise", "noise_peaking_times", "se_power", "se_rails",
              "se_pulse", "diff_pulse", "diff_power", "diff_rails", "monitor_path"]
DATA_TESTS = ["noise", "noise_peaking_times", "se_pulse", "diff_pulse"]   # capture ADC data
SETTING_NAMES = ["tests", "femb_ids", "dc2dc_V", "safety_A", "monitor_samples", "monitor_lsb_mV",
                 "data_samples", "settle_s", "toy_tpc", "i2c_tries"]
DC2DC_SENSORS = ["DC2DC0", "DC2DC1", "DC2DC2"]    # the FEMB's three DC-DC supplies (LArASIC, COLDATA, ColdADC)

# Power rail name used in config.json -> sensor name in chk.get_sensors()
POWER_RAILS = {"BIAS": "BIAS", "LArASIC": "DC2DC0", "ColdDATA": "DC2DC1", "ColdADC": "DC2DC2"}

MONITOR_NAMES = ["VSSA", "FE T", "FE BGP", "ADC_VCMI", "ADC_VCMO", "ADC_VREFP", "ADC_VREFN"]

# BNL front end configurations (copied from femb_assembly_chk.py). All are 14 mV/fC.
SE_200MV = {"snc": 1, "sts": 0, "swdac": 0, "dac": 0x00, "pulse": 0}                     # 3.1, 3.2, 3.3
SE_900MV_PULSE = {"snc": 0, "sts": 1, "swdac": 1, "dac": 0x10, "pulse": 1}               # 3.4, 5
DIFF_900MV_PULSE = {"snc": 0, "sts": 1, "swdac": 1, "dac": 0x10, "pulse": 1, "diff": True}  # 4.1-4.3
PEAKING_TIMES = {"0_5us": (1, 0), "1_0us": (0, 0), "2_0us": (1, 1), "3_0us": (0, 1)}       # name: (st0, st1)


def parse_positions(args):
    """
    FEMB positions from the command line, e.g. ["0", "2"] -> [0, 2]
    """
    if not args:
        raise ValueError("Give at least one FEMB position (0-3), e.g. python3 wiec_femb_chk.py 0 1 2 3")
    positions = [int(arg) for arg in args]
    for position in positions:
        if position not in range(FEMB_POSITIONS):
            raise ValueError(f"FEMB position {position} is not 0-{FEMB_POSITIONS - 1}")
    return positions


def read_settings():
    """
    The settings femb_checkout.py sends on stdin (see the module docstring).
    Returns (settings, tests) where tests is {test name: True/False} for every test.
    """
    text = sys.stdin.read()
    if not text.strip():
        raise ValueError("No settings on stdin. femb_checkout.py sends them; by hand use "
                         "python3 wiec_femb_chk.py 0 < settings.json")
    settings = json.loads(text)

    missing = [name for name in SETTING_NAMES if name not in settings]
    if missing:
        raise ValueError(f"Settings are missing {missing}")
    unknown = [name for name in settings["tests"] if name not in TEST_NAMES]
    if unknown:
        raise ValueError(f"Unknown test names {unknown}; known tests are {TEST_NAMES}")

    tests = {name: bool(settings["tests"].get(name, False)) for name in TEST_NAMES}
    settings["femb_ids"] = {int(position): serial for position, serial in settings["femb_ids"].items()}
    return settings, tests


# ----------------------------------------------------------------------
# FEMB power and configuration
# ----------------------------------------------------------------------
def presence_check(chk, positions, min_current_a):
    """
    A FEMB that is not plugged in draws no current. Switches such positions off straight
    away, before anything tries to configure them. Returns {position: [reason]}.
    """
    sensors = chk.get_sensors()
    missing = {}
    for position in positions:
        current = sum(sensors[f"FEMB{position}_{sensor}_I"] for sensor in DC2DC_SENSORS)
        if current < min_current_a:
            print(f"FEMB position {position} draws {current:.3f} A: not connected. Powering it off.")
            chk.femb_powering_single(position, "off")
            missing[position] = [f"not detected: DC-DC current {current:.3f} A (FEMB or power cable not connected?)"]
    return missing


def configure_femb(chk, position, pulse, max_tries):
    """
    BNL's WIB_CFGS.femb_cfg with two changes: it gives up after max_tries I2C retries
    (BNL: 100, minutes per FEMB), and on failure it does not switch off every FEMB.
    Returns True if the FEMB is configured.
    """
    chk.i2cerror = False    # BNL keeps one flag for all FEMBs: clear a failure left by the previous one
    if chk.cd_flg[position] and not chk.femb_cd_cfg(position):
        return False
    if chk.adc_flg[position] and not chk.femb_adc_cfg(position):
        return False
    if chk.fe_flg[position] and not chk.femb_fe_cfg(position):
        return False

    if chk.i2cerror:
        good_in_row = 0
        for attempt in range(1, max_tries + 1):
            chk.i2cerror = False
            if attempt % 5 == 0:
                chk.wib_i2c_adj(n=50)            # qift the I2C phase, as BNL does every 5th try
            time.sleep(0.2)
            chk.femb_cd_cfg(position)
            chk.femb_adc_cfg(position)
            chk.femb_fe_cfg(position)
            good_in_row = 0 if chk.i2cerror else good_in_row + 1
            if good_in_row >= 5:                 # BNL: 5 clean configurations in a row
                break
        else:
            print(f"FEMB position {position}: I2C configuration failed after {max_tries} tries")
            return False

    if pulse and not chk.adac_cali_quo[position]:
        chk.femb_adac_cali(position)
    print(f"FEMB{position} is configurated")
    return True


def power_on(chk, positions, settings):
    """
    BNL Part 2 start: all FEMBs off, set the DC-DC voltages, power on, check each FEMB is
    there, then the default configuration. Returns {position: [reasons]} for every FEMB
    that was switched off because it is not connected or does not answer on I2C.
    """

    dc2dc_v = settings["dc2dc_V"]
    print("Power off FEMBs to start from a known state")
    chk.femb_powering([])
    chk.fembs_vol_set(vfe=dc2dc_v["LArASIC"], vcd=dc2dc_v["ColdDATA"], vadc=dc2dc_v["ColdADC"])

    print(f"Power on FEMB positions {positions}")
    chk.femb_power_com_on(positions)
    time.sleep(0.5)

    dropped = presence_check(chk, positions, settings["safety_A"]["presence_min_A"])
    connected = [position for position in positions if position not in dropped]
    if not connected:
        return dropped

    chk.wib_femb_link_en(connected)     # the legacy cable test enabled the data links before configuring
    chk.femb_cd_rst()
    for chip in range(8):
        chk.adcs_paras[chip][8] = 1    # ColdADC auto calibration on
    for position in connected:
        if not configure_femb(chk, position, False, settings["i2c_tries"]):
            chk.femb_powering_single(position, "off")
            dropped[position] = [f"I2C configuration failed after {settings['i2c_tries']} tries "
                                 "(read-back errors: data cable, connector or flange path)"]
    return dropped



def configure_fembs(chk, positions, i2c_tries, snc, sts, swdac, dac, pulse, st0=1, st1=1, diff=False):
    """
    Reset and configure the FEMBs: 14 mV/fC, peaking time from st0/st1 (default 2 us),
    single ended or differential (diff) ColdADC input. These FEMBs all configured at power
    on, so one that stops answering now is a real fault: the run stops (FEMBs are switched
    off by main's finally) and femb_checkout.py reports that the checkout did not finish.
    """
    chk.femb_cd_rst()
    for chip in range(8):
        chk.adcs_paras[chip][2] = 1 if diff else 0    # ColdADC differential input
        chk.adcs_paras[chip][8] = 1                   # ColdADC auto calibration on
    for position in positions:
        chk.set_fe_board(sts=sts, snc=snc, sg0=0, sg1=0, st0=st0, st1=st1,
                         sdd=1 if diff else 0, swdac=swdac, dac=dac)
        if not configure_femb(chk, position, pulse, i2c_tries):
            raise RuntimeError(f"FEMB position {position} stopped answering on I2C during the checkout")


# ----------------------------------------------------------------------
# Power, registers, rails, monitor path
# ----------------------------------------------------------------------
def measure_power(chk, positions):
    """
    BNL 2.1 / 3.2 / 4.2: voltage, current and power of each rail
    Returns {position: {rail: {"V": .., "A": .., "W": ..}}}
    """
    sensors = chk.get_sensors()
    power = {}
    for position in positions:
        rails = {}
        for rail, sensor in POWER_RAILS.items():
            volts = sensors[f"FEMB{position}_{sensor}_V"]
            amps = sensors[f"FEMB{position}_{sensor}_I"]
            rails[rail] = {"V": round(volts, 3), "A": round(amps, 3), "W": round(volts * amps, 3)}
        power[position] = rails
    return power


def safety_cut(chk, power, safety_a):
    """
    Switches off any FEMB whose current is clearly wrong, as BNL Part 2.1 does.
    Always runs, even when the power test is switched off: it protects the FEMBs.
    Returns {position: [reasons]} for every FEMB that was switched off.
    """
    dropped = {}
    for position, rails in power.items():
        reasons = []
        if abs(rails["BIAS"]["A"]) > safety_a["BIAS_abs_max"]:
            reasons.append(f"BIAS current {rails['BIAS']['A']} A")
        if rails["LArASIC"]["A"] < safety_a["LArASIC_min"]:
            reasons.append(f"LArASIC current {rails['LArASIC']['A']} A")
        if rails["ColdDATA"]["A"] > safety_a["ColdDATA_max"]:
            reasons.append(f"ColdDATA current {rails['ColdDATA']['A']} A")

        if reasons:
            print(f"FEMB position {position} failed the safety current check ({', '.join(reasons)}). Powering it off.")
            chk.femb_powering_single(position, "off")
            dropped[position] = reasons
    return dropped


def chip_reset(chk, positions):
    """
    Copy of BNL assembly_function.chip_reset: reset COLDATA, ColdADC and LArASIC
    """
    chk.femb_cd_rst()
    chk.femb_cd_rst()
    time.sleep(0.1)
    for position in positions:
        chk.femb_cd_fc_act(position, act_cmd="rst_adcs")
        time.sleep(0.01)
        chk.femb_cd_fc_act(position, act_cmd="rst_adcs")
        time.sleep(0.01)
        chk.femb_cd_fc_act(position, act_cmd="rst_larasics")
        chk.femb_cd_fc_act(position, act_cmd="rst_larasics")
        time.sleep(0.01)
        chk.femb_cd_fc_act(position, act_cmd="rst_larasic_spi")
        chk.femb_cd_fc_act(position, act_cmd="rst_larasic_spi")
    time.sleep(0.1)


def check_registers(chk, positions):
    """
    BNL Part 2.2: reset, check default registers, reset, check again.
    A chip fails if either pass finds a wrong register.
    Returns {position: {"COLDATA": bool, "ColdADC": bool}}  (True = registers correct)
    """
    results = {position: {"COLDATA": True, "ColdADC": True} for position in positions}
    for attempt in (1, 2):
        print(f"Register check {attempt} of 2")
        chip_reset(chk, positions)
        for position in positions:
            if chk.femb_cd_chkreg(position):
                results[position]["COLDATA"] = False
            adc_error, _ = chk.femb_adc_chkreg(position)
            if adc_error:
                results[position]["ColdADC"] = False
    return results


def trimmed_mean(samples, position):
    """
    Mean of one FEMB's monitor ADC readings with the highest and lowest removed (as BNL does for rails).
    samples is a list of 4-value readings, one value per FEMB position.
    """
    values = [reading[position] for reading in samples]
    values.remove(max(values))
    values.remove(min(values))
    return sum(values) / len(values)


def plain_mean(samples, position):
    """
    Mean of one FEMB's monitor ADC readings (as BNL does for the monitor path)
    """
    return sum(reading[position] for reading in samples) / len(samples)


def to_mv(samples, position, ground, lsb_mv):
    """
    Monitor path reading in mV for one FEMB, ground offset subtracted
    """
    return round(plain_mean(samples, position) * lsb_mv - ground, 1)


def measure_rails(chk, positions, configuration, samples, lsb_mv, i2c_tries):
    """
    BNL 3.3 / 4.3: FEMB LDO voltages in mV under `configuration`, ground offset subtracted.
    "_HALF" rails are read through a divide by 2, so they are doubled.
    Returns {position: {rail name: mV}}
    """
    configure_fembs(chk, positions, i2c_tries, **configuration)
    chk.wib_mon_switches(dac0_sel=1, dac1_sel=1, dac2_sel=1, dac3_sel=1, mon_vs_pulse_sel=0, inj_cal_pulse=0)
    raw = chk.wib_vol_mon(femb_ids=positions, sps=samples)

    rails = {}
    for position in positions:
        gnd = trimmed_mean(raw["GND"], position)
        values = {"GND": abs(round(gnd * lsb_mv))}
        for name, readings in raw.items():
            if name == "GND":
                continue
            millivolts = (trimmed_mean(readings, position) - gnd) * lsb_mv
            if "HALF" in name:
                millivolts *= 2
            values[name] = abs(round(millivolts))
        rails[position] = values
    return rails


def measure_monitor_path(chk, positions, ground_mv, samples, lsb_mv, i2c_tries):
    """
    BNL Part 5: per ASIC chip (8 per FEMB) the FE temperature, FE bandgap and ColdADC
    reference voltages, in mV with each FEMB's ground offset subtracted.
    Returns {position: {name: [chip0, ..., chip7]}}
    """
    configure_fembs(chk, positions, i2c_tries, **SE_900MV_PULSE)   # BNL Part 5 setup

    bandgap = {}
    temperature = {}
    adc_refs = {}
    print("Monitor FE bandgap reference")
    for chip in range(8):
        bandgap[chip] = chk.wib_fe_mon(femb_ids=positions, mon_type=2, mon_chip=chip, snc=0, sg0=0, sg1=0, sps=samples)[7]
    print("Monitor FE temperature")
    for chip in range(8):
        temperature[chip] = chk.wib_fe_mon(femb_ids=positions, mon_type=1, mon_chip=chip, snc=0, sg0=0, sg1=0, sps=samples)[7]
    print("Monitor ColdADC references")
    for chip in range(8):
        adc_refs[chip] = chk.wib_adc_mon_chip(femb_ids=positions, mon_chip=chip, sps=samples)

    results = {}
    for position in positions:
        ground = ground_mv[position]
        values = {name: [] for name in MONITOR_NAMES}
        for chip in range(8):
            values["VSSA"].append(to_mv(adc_refs[chip]["VSSA"][1], position, ground, lsb_mv))
            values["FE T"].append(to_mv(temperature[chip], position, ground, lsb_mv))
            values["FE BGP"].append(to_mv(bandgap[chip], position, ground, lsb_mv))
            values["ADC_VCMI"].append(to_mv(adc_refs[chip]["VCMI"][1], position, ground, lsb_mv))
            values["ADC_VCMO"].append(to_mv(adc_refs[chip]["VCMO"][1], position, ground, lsb_mv))
            values["ADC_VREFP"].append(to_mv(adc_refs[chip]["VREFP"][1], position, ground, lsb_mv))
            values["ADC_VREFN"].append(to_mv(adc_refs[chip]["VREFN"][1], position, ground, lsb_mv))
        results[position] = values
    return results


# ----------------------------------------------------------------------
# ADC data tests, analysed by BNL's own code
# ----------------------------------------------------------------------
class BnlAnalysis:
    """
    Wraps BNL's analysis for the ADC data tests: creates BNL's data and report
    folders (CHK/ and CHK/Report/), runs rms_ped_ana / se_pulse_ana /
    DIFF_pulse_data, and reads BNL's verdict and summary numbers back out of
    BNL's log tables. Only created when an ADC data test is switched on.
    """

    def __init__(self, chk, positions, settings):
        # Imported here: the default tests do not need BNL's analysis code (or matplotlib).
        import components.assembly_function as a_func
        import components.assembly_log as log
        self.a_func, self.log = a_func, log
        self.chk, self.positions, self.settings = chk, positions, settings

        self.femb_no = {f"femb{position}": settings["femb_ids"][position] for position in positions}
        datadir = a_func.Create_data_folders(self.femb_no, "RT", settings["toy_tpc"])
        self.report_dirs = a_func.Create_report_folders(positions, self.femb_no, "RT", settings["toy_tpc"], datadir)
        chk.wib_femb_link_en(positions)    # BNL enables the data links before its first capture

    def _label(self, position):
        return f"FEMB ID {self.settings['femb_ids'][position]}"    # BNL's key in its log tables

    def capture(self, configuration):
        """Configure, let it settle, align the data links and read the spy buffers."""
        configure_fembs(self.chk, self.positions, self.settings["i2c_tries"], **configuration)
        time.sleep(self.settings["settle_s"])
        self.chk.data_align(self.positions)
        return self.chk.spybuf_trig(fembs=self.positions, num_samples=self.settings["data_samples"], trig_cmd=0)

    def noise(self, configuration, fname):
        """BNL 3.1: pedestal and RMS of all 128 channels. Returns {position: {"bnl_pass", "values"}}."""
        self.a_func.rms_ped_ana(self.capture(configuration), self.positions, self.femb_no, self.report_dirs, fname)
        results = {}
        for position in self.positions:
            label = self._label(position)
            data = self.log.report_log04csv[label]
            ped, rms = list(data.get("ped", [])), list(data.get("rms", []))
            results[position] = {
                "bnl_pass": bool(self.log.report_log04[label].get("Result", False)),
                "values": {
                    "ped_mean": round(sum(ped) / len(ped), 1) if ped else None,
                    "rms_mean": round(sum(rms) / len(rms), 2) if rms else None,
                    "rms_min": round(min(rms), 2) if rms else None,
                    "rms_max": round(max(rms), 2) if rms else None,
                },
            }
        return results

    def noise_peaking_times(self):
        """BNL 3.1 'sp': RMS at the four peaking times, SE 900 mV. One verdict and rms_mean per peaking time."""
        results = {position: {"bnl_pass": True, "values": {}} for position in self.positions}
        for name, (st0, st1) in PEAKING_TIMES.items():
            configuration = {"snc": 0, "sts": 0, "swdac": 0, "dac": 0x00, "pulse": 0, "st0": st0, "st1": st1}
            for position, one in self.noise(configuration, f"Raw_SE_900mVBL_14_0mVfC_{name}_0x00").items():
                results[position]["bnl_pass"] = results[position]["bnl_pass"] and one["bnl_pass"]
                results[position]["values"][f"rms_mean_{name}"] = one["values"]["rms_mean"]
        return results

    def se_pulse(self):
        """BNL 3.4: SE pulse response. BNL's verdict plus mean positive peak, negative peak, baseline."""
        self.a_func.se_pulse_ana(self.capture(SE_900MV_PULSE), self.positions, self.femb_no, self.report_dirs,
                                 "Raw_SE_900mVBL_14_0mVfC_2_0us_0x10.bin")
        results = {}
        for position in self.positions:
            label = self._label(position)
            data = self.log.report_log07csv[label]
            results[position] = {
                "bnl_pass": bool(self.log.report_log07[label].get("Result", False)),
                "values": {"ppk_mean": data.get("PPK_mean"), "npk_mean": data.get("NPK_mean"),
                           "bl_mean": data.get("BL_mean")},
            }
        return results

    def diff_pulse(self):
        """BNL 4.1: DIFF pulse response. BNL keeps no summary numbers for it, only its verdict."""
        self.a_func.DIFF_pulse_data(self.capture(DIFF_900MV_PULSE), self.positions, self.femb_no, self.report_dirs,
                                    "Raw_DIFF_900mVBL_14_0mVfC_2_0us_0x10")
        return {position: {"bnl_pass": bool(self.log.report_log08[self._label(position)].get("Result", False)),
                           "values": {}}
                for position in self.positions}


# ----------------------------------------------------------------------
# Running the selection
# ----------------------------------------------------------------------
def run_selected_tests(chk, positions, tests, settings, result):
    """Runs every switched-on test on the FEMBs that passed the safety cut, in BNL's order."""
    samples, lsb_mv, i2c_tries = settings["monitor_samples"], settings["monitor_lsb_mV"], settings["i2c_tries"]

    if tests["registers"]:
        result["registers"] = check_registers(chk, positions)

    bnl = BnlAnalysis(chk, positions, settings) if any(tests[name] for name in DATA_TESTS) else None
    if bnl:
        result["report_dir"] = "CHK/Report"

    if tests["noise"]:
        result["noise"] = bnl.noise(SE_200MV, "Raw_SE_200mVBL_14_0mVfC_2_0us_0x00")
    if tests["noise_peaking_times"]:
        result["noise_peaking_times"] = bnl.noise_peaking_times()

    if tests["se_power"]:
        configure_fembs(chk, positions, i2c_tries, **SE_200MV)
        result["se_power"] = measure_power(chk, positions)

    se_rails = None
    if tests["se_rails"] or tests["monitor_path"]:
        se_rails = measure_rails(chk, positions, SE_200MV, samples, lsb_mv, i2c_tries)
        if tests["se_rails"]:
            result["se_rails"] = se_rails

    if tests["se_pulse"]:
        result["se_pulse"] = bnl.se_pulse()
    if tests["diff_pulse"]:
        result["diff_pulse"] = bnl.diff_pulse()

    if tests["diff_power"]:
        configure_fembs(chk, positions, i2c_tries, **DIFF_900MV_PULSE)
        result["diff_power"] = measure_power(chk, positions)
    if tests["diff_rails"]:
        result["diff_rails"] = measure_rails(chk, positions, DIFF_900MV_PULSE, samples, lsb_mv, i2c_tries)

    if tests["monitor_path"]:
        ground_mv = {position: se_rails[position]["GND"] for position in positions}
        result["monitor_path"] = measure_monitor_path(chk, positions, ground_mv, samples, lsb_mv, i2c_tries)


def main():
    positions = parse_positions(sys.argv[1:])
    settings, tests = read_settings()
    start = time.time()

    # femb_checkout.py ends this script with `timeout` (SIGTERM) when it overruns, and a closed
    # ssh connection sends SIGHUP. Either one becomes a normal exit, so the finally below still
    # switches the FEMBs off.
    for stop_signal in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(stop_signal, lambda number, frame: sys.exit(f"Stopped by signal {number}"))

    chk = WIB_CFGS()
    chk.wib_fw()

    result = {"positions": positions, "tests": tests, "dropped": [], "dropped_reasons": {}, "report_dir": None}
    for name in TEST_NAMES:
        result[name] = {}
    try:
        result["dropped_reasons"] = power_on(chk, positions, settings)      # not connected / no I2C
        connected = [position for position in positions if position not in result["dropped_reasons"]]
        power = measure_power(chk, connected) if connected else {}
        result["dropped_reasons"].update(safety_cut(chk, power, settings["safety_A"]))   # always, for safety
        result["dropped"] = list(result["dropped_reasons"])
        if tests["power"]:
            result["power"] = power

        tested = [position for position in positions if position not in result["dropped"]]
        if tested:
            run_selected_tests(chk, tested, tests, settings, result)
    finally:
        print("Turning off FEMBs")
        chk.femb_powering([])

    print(f"Time: {round(time.time() - start, 1)} s")
    print(RESULT_MARKER + " " + json.dumps(result))


if __name__ == "__main__":
    main()
