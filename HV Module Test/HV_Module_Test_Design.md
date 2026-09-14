# DUNE WIEC HV Module Test — Design Record

Record of the design decisions for the new `hv_module_test` item in
`run_wiec_component.py`.  Written down so the work survives a PC restart.

- Date: 2026-08-20
- Item key: `hv_module_test`
- Code: `WIEC_Component/iseg_mpod.py`, `WIEC_Component/operator.py`,
  `WIEC_Component/hv_module_test.py`
- Outputs: `HV Module Test/results/<timestamp>_<serial>/`, including a
  self-contained `report.html` built automatically at the end of every run
  (`WIEC_Component/hv_module_report.py` rebuilds it standalone).  `--dry-run`
  writes to `HV Module Test/dry-run/` instead, so simulated output can never
  be mistaken for — or delete — measured data
- Spreadsheet: `HV Module Test/hv_module_test_results.xlsx` (separate workbook,
  does **not** touch `DUNE_HV_Crate_Production_Test.xlsx`)
- Development log: `WIEC_Component/HV_MODULE_TEST_DEVLOG.md` — how this design
  was arrived at, and the approaches that looked right but failed against real
  hardware.  **Read it before changing any of this.**

---

## 1. What this item tests

One iseg HV module (8 channels) inserted into **slot 2** of the WIENER MPOD
crate, driven over SNMP.  This is a *module* test, not the crate/PCB test the
other `WIEC_Component` items run, so it is registered in `EXTRA_ITEMS`, not in
`CRATE_ITEMS`.

Slot 2 maps to module `ma2` and channels `u200 … u207`.

## 2. Step model — three kinds only

Every step is one of three kinds (per the agreed test-flow proposal):

| Kind | Meaning |
|---|---|
| `AUTO` | Software does it, reads it and judges it. No operator involvement. |
| `USER` | Operator has to do something with their hands, then clicks Continue. |
| `CHECK` | Operator looks at the hardware and answers PASS/FAIL (or Yes/No). |

The flow is **data, not code**: a list of `Step` objects that a single engine
walks.  Two consequences that matter:

- the three jumper-loop repetitions are generated from one function instead of
  copy-pasted three times;
- a future GUI (`wiec_crate_gui.py`) reuses the same list and only replaces the
  two operator-facing functions.  The flow itself does not change.

**A USER step never advances on a bare Enter.**  The operator has just been
asked to do something physical, and Enter is the reflex used to skip past
prompts, so the answer has to be a real word: `y` once the work is actually
done, or `q` to stop the test.  The same applies to the typed serial number and
to the PASS/FAIL of a CHECK step, and `q` is accepted at all three so an
operator who spots a problem can stop cleanly instead of reaching for Ctrl-C.
The "carry on after a safe failure?" question has no default either.

Operator prompts are shown as one large box and nothing else, so the operator
does not need the SOP open:

```
╔════════════════════════════════════════════════╗
║            USER ACTION REQUIRED                ║
╠════════════════════════════════════════════════╣
║  Remove the test kit.                          ║
║  Keep the jumper installed.                    ║
║  Reinsert the module into Slot 2.              ║
╚════════════════════════════════════════════════╝
              Done?  type y to confirm, q to stop:
```

## 3. Decisions taken (中文原始决定)

| # | Question | Decision |
|---|---|---|
| 1 | Ramp all 8 channels together or one at a time? | **All 8 together, one test in total.** Per-channel data is still recorded separately so a bad channel can be identified. 一次测完 8 个通道，但每通道单独记录。 |
| 2 | How is the crate powered down for module insertion? | **The PC does it** — `sysMainSwitch` really does power the module bay down (fans stop, 0 modules enumerated), so nobody touches the rear panel. Superseded the earlier "AC by hand" decision once this was measured; see section 4. 电脑关就够了，风扇会停。 |
| 3 | Where do results go? | **A separate workbook**, `HV Module Test/hv_module_test_results.xlsx`. The crate workbook's column layout is fixed for the crate test and must not be polluted. 独立表格。 |
| 4 | Serial number handling | **Operator types the expected SN first**, software reads the real one over SNMP and compares. Mismatch raises a loud warning showing both numbers. 先输入再比对，不符则警告。 |
| 5 | What happens on a failed step? | **Safe failure → record, warn, ask whether to continue. Unsafe failure → ramp down, power off, record, loud warning, abort.** 安全就记录继续，不安全就停止并警告。 |

### 5 in detail — the safety classification

| Severity | Steps | On failure |
|---|---|---|
| `safe` | visual inspection, SN comparison, all green-LED checks (module is unpowered or at low voltage) | record FAIL → warn → ask the operator whether to continue → keep going, overall verdict is FAIL |
| `unsafe` | HV ramp, HV limit, and any trip / overcurrent / target voltage not reached / SNMP communication loss | ramp to 0 V → `channel_off` → `sysMainSwitch=0` → record FAIL → red warning box → abort the item |

The ramp-down path lives in a `try/finally`, so Ctrl-C and an unhandled
exception take the same route.  Nobody can walk away from a crate sitting at
1 kV.

## 4. AC mains vs `sysMainSwitch` — why both exist

Two different things are called "power off" on an MPOD:

1. **`sysMainSwitch` (SNMP, scriptable).**  `WIENER-CRATE-MIB` says:
   *"Write: Try to switch the complete crate ON or OFF."*  It cuts the supply
   to the modules, but the **MPOD controller stays alive** — that is precisely
   why the crate can be switched back on over the network afterwards.  The
   backplane and the controller remain energised.
2. **The AC mains switch on the rear panel (hand only).**  This removes mains
   from the whole crate, controller included.  There is no networked PDU in
   this setup (`ip_addresses.json` lists only the CAEN, Keysight, PL506 and the
   two Rigols), so **no script can do this**.

**Measured (2026-08-20): `sysMainSwitch` is enough.**  Switching it off stops
the fans and takes the module bay completely down — the controller enumerates
0 modules and 0 outputs and reports `inputFailure`.  Switching it back on
brings the bay up again in about 13 s.  A full cycle:

```
ON   t= 0.0s  0 modules   0 outputs   slot 2 absent
     t= 2.1s  2 modules  16 outputs   slot 2 absent
     t=10.5s  4 modules  32 outputs   slot 2 absent
     t=12.6s  5 modules  40 outputs   slot 2 PRESENT
OFF  t= 0.1s  0 modules   0 outputs   bay dead
```

So the default is `iseg_power_cycle_mode = "snmp"`: the software powers the
crate down and up by itself and nobody touches the rear panel.  This replaces
the original decision, which was taken before the behaviour had been measured.
`"manual_mains"` is still supported for a site that requires a true AC
disconnect; the only difference is an extra USER step asking for the switch.

**Both modes run the same verification.**  The bay must be *proven* dead before
a module is handled, whether the software or a person switched it off — an
`snmp` mode that just fired sysMainSwitch and moved on would be worse than the
manual one.  And because the SNMP table clears the instant the bay goes off
while the fans take a few seconds to stop, `Confirm Crate Is Off` then waits
`iseg_power_off_settle_s` (10 s by default) with a visible countdown.

Power-up gets the mirror treatment: after `moduleDescription.ma<slot>` appears
the step waits `iseg_power_on_settle_s` (5 s by default) before anything reads
or writes the module's settings.  The bay enumerates progressively and slot 2
comes up behind other slots, so "the description exists" is the earliest
possible moment, not a settled one.  Both waits print a countdown — a silent
deliberate pause is indistinguishable from a hang.

The software still helps and still verifies:

- before prompting, it sets `sysMainSwitch=0` itself, so the outputs are
  already down when the mains goes;
- it then **polls until the crate stops answering**, which catches the operator
  who typed `y` without actually flipping the switch;
- after power-up it **polls until SNMP answers again** rather than sleeping a
  fixed number of seconds.

### The controller does not die with the mains — how "off" is really checked

Measured on this crate (2026-08-20), with the operator's power switch thrown:

```
sysMainSwitch = off(0)
sysStatus     = inputFailure(3)
moduleNumber  = 0          (was 5)
outputNumber  = 0          (was 40)
moduleDescription -> the whole table is empty
psSerialNumber = 1084023   <- the controller is still answering
```

The **MPOD controller keeps running on standby** after the supply is switched
off.  The first implementation waited for SNMP to go silent as proof that the
mains was off; on this crate that **never happens**, so the step hung until it
timed out even though the operator had done exactly the right thing.

The check now looks for **positive evidence that the module bay is dead**
instead of for an absence:

- the crate not answering at all is still accepted, but
- so is "controller answering, `sysMainSwitch` off, **0 modules and 0
  outputs enumerated**".  A module cannot be enumerated over the internal bus
  without bay power, so zero modules is direct evidence the backplane is dead.

`iseg_mpod.module_bay_powered()` implements this and returns the reason string
that goes into the results.

**The bay enumerates progressively.**  After power-up `moduleNumber` climbs
towards its final value over several seconds, so waiting for `moduleNumber > 0`
can let the flow carry on while the slot under test is still missing from the
table.  Power-up therefore waits for `moduleDescription.ma<slot>` to exist, not
just for a non-zero count.  (The same counts run *downwards* through
5 → 2 → 0 during power-down, which is easy to mistake for a crate coming up.)

`Confirm Crate Is Off` is the one step **the operator cannot answer by typing**.
The step before it already asked for confirmation; this one checks that claim
electrically, and an override would make it pointless.  Both waits print a live
line — elapsed time and whether the crate is currently answering — so a long
wait never looks like a hang, and both end within one poll of the crate
actually changing state.

A timeout is **not** an instant abort.  Forgetting the switch is an ordinary
mistake, and throwing away a six-step run for it would only tempt people to
skip the check.  Instead the operator gets a warning box explaining what is
wrong, and can flip the switch and let the software look again; the step only
fails, and only then aborts the run, if the operator chooses to stop waiting.
The same applies to a crate that does not come back up.

Config key: `iseg_power_cycle_mode`, `"manual_mains"` (default) or `"snmp"`.

## 5. Findings from the live crate (2026-08-20, read-only probe)

Crate `169.254.4.31`, `sysMainSwitch = on`, `psSerialNumber = 1084023`,
40 outputs, 5 modules:

```
ma0 = WIENER, MPV8016, 8, 0010419
ma2 = iseg, E08F2, 08, 7200489, 07.52     <- slot 2, the module under test
ma7 = iseg, E08C2, 08, 7800165, 04.56
ma8 = iseg, E08C2, 08, 7800166, 04.56
ma9 = WIENER, MPV8008, 8, 0010531
```

### 5.1 The safety jumper and the LED are both readable over SNMP

Measured across all three configurations:

```
moduleSafetyLoopIsGood(10) = the jumper (and latches moduleIsEventActive(11))
moduleIsGood(12)           = jumper AND test kit = exactly the green LED
```

So each operator LED check is backed by an `AUTO` step that reads the same
condition independently, and the jumper itself is checked directly.  The
operator still answers the LED question — a disagreement between the two is
what makes the pair worth having.

Working this out took two wrong guesses and two failed runs; the devlog's
§5.0 records why, and it is worth reading before touching these expectations.

### 5.2 The hardware limit is a percentage, not a voltage

`outputHardwareLimitVoltage` / `outputHardwareLimitCurrent` (per channel) are
**not implemented by this controller** — `No Such Object`.  The module-level
ones are:

```
moduleHardwareLimitVoltage.ma2 = 102.0 %     <- the voltage potentiometer
moduleHardwareLimitCurrent.ma2 = 102.1 %     <- the current potentiometer
moduleRampSpeedVoltage.ma2     = 5.0 %       <- = 100 V/s on a 2 kV module
```

They read as **percent of the module's nominal full scale**, and this matches
the limit-test specification exactly:

| Pot control voltage | Percent | 2 kV module |
|---|---|---|
| 2.5 V | 100 % | 2.0 kV |
| 1.5625 V | 62.5 % | 1.25 kV |

So the HV limitation test can **read the potentiometer setting directly**
(expect ≈ 62.5 %) as well as verifying that the output reaches 1.2 kV and does
not reach 1.3 kV.

`moduleRampSpeedVoltage` being a percentage also means **10 V/s = 0.5 %** and
100 V/s = 5 % on a 2 kV module.  The item writes the ramp rate per channel in
V/s and reads this percentage back only as a cross-check — see 5.5.

**Confirmed (2026-08-20):** the proposal's `0.15625 V → 4 mA` was a typo.  By
the same linear relation 0.15625 V is 6.25 % of full scale, i.e. **0.25 mA** on
a 4 mA module.  The limit test checks both pots:

| Pot | Control voltage | Percent | On this module |
|---|---|---|---|
| voltage | 1.5625 V | 62.5 % | 1.25 kV of 2 kV |
| current | 0.15625 V | 6.25 % | 0.25 mA of 4 mA |

Config: `iseg_limit_expected_voltage_percent` / `iseg_limit_expected_current_percent`.

### 5.3 The slot-2 module is negative polarity

```
outputConfigMaxSenseVoltage.u200 = -2000 V
outputVoltageRiseRate.u200       = -100 V/s
```

Polarity is therefore **detected automatically** from the sign of
`outputConfigMaxSenseVoltage`, not typed in by the operator and not hard-coded.
It is recorded with the results and used to interpret the readbacks; the
setpoints themselves are written as magnitudes (see 5.5).

### 5.4 Serial number source

`moduleSerialNumber` does **not** exist on this agent.  The serial number is
parsed out of `moduleDescription.ma2` (field 4 of
`"iseg, E08F2, 08, 7200489, 07.52"`).

## 5.5 Corrections found while implementing (all verified on the crate)

- **Setpoints are written as magnitudes.**  `outputVoltage.u200` reads back
  `+40 V` on a module whose full scale is `-2000 V`, and the working standalone
  QC scripts write positive numbers.  `set_voltage()` therefore writes
  `abs(voltage)` and every comparison is done on magnitudes.  Writing a signed
  setpoint was the original plan and would have been wrong.
- **The ramp rate must be written as the module-level percentage**,
  `moduleRampSpeedVoltage.maN`, not per channel.  Writing
  `outputVoltageRiseRate.uNNN` looks like it works — snmpset returns success —
  but the value never changes: a commanded 10 V/s still read back as 100 V/s,
  which is exactly the 5% the module was already set to.  The MIB says why:
  *"iseg HV modules have the same ramp speed value for all channels"*, and one
  per-channel OID cannot express a module-wide setting.  This was found the
  hard way on the first real run — the readback guard caught it and refused to
  ramp, which is what the guard is for.  Verified on hardware after the fix:

  ```
  set  10 V/s -> wrote 0.500 % | module reads 0.500 % | u200  10.00 V/s  u207  10.00 V/s
  set 100 V/s -> wrote 5.000 % | module reads 5.000 % | u200 100.00 V/s  u207 100.00 V/s
  ```

  10 V/s is 0.5% of full scale and is **not** clamped by this module.
- **A tripped channel will not ramp until its events are cleared**:
  `outputSwitch = clearEvents(10)`, written per channel before the ramp.
- `outputStatus` bit numbers had to be taken from the MIB rather than guessed;
  `outputAdjusting` is bit 15, and normal states (`outputRampUp`,
  `outputConstantVoltage`, `outputLowCurrentRange`) must not be treated as
  faults.  Only bits 1, 2-7, 9, 14, 19 and 25 abort a test.
- Reading all 8 channels with three `snmpwalk`s instead of 24 `snmpget`s takes
  **0.09 s** per sample, which is what makes 1 Hz sampling through the ramp
  practical.
- The slot 2 module's voltage potentiometer currently reads **102 %** (≈2040 V),
  i.e. wide open — not the 62.5 % the limitation test expects.  Whoever sets up
  that test has to turn the pot down first.

## 6. Changes required outside the new files

| File | Change | Why |
|---|---|---|
| `WIEC_Component/session.py` | lazy `mpod` property + an MPOD step in `emergency_shutoff()` and in `shutdown_only()` | **Real safety gap today:** `emergency_shutoff()` knows nothing about the ISEG, so Ctrl-C during a ramp leaves the MPOD energised |
| `WIEC_Component/preflight.py` | ping the MPOD; check `snmpget`/`snmpset` exist | the item is useless without net-snmp installed |
| `config.json` | `iseg_*` keys (IP was hard-coded as `169.254.4.31` in two root scripts) | no ISEG entry existed at all |
| `run_wiec_component.py` | import the item, add to `EXTRA_ITEMS` | registers it in the menu, `--items`, `--list` |
| `iseg_pmod_wrapper_QC.py` | `turn_off_crate` called `self.snmp_int`, which does not exist | real bug: `AttributeError` every time the crate is switched off |

## 7. The flow as implemented

```
1. Visual Inspection
   USER   Open Box
   CHECK  Check Components                 (safe)
   USER   Install Test Kit and Jumper

2. Insert Module
   USER   Enter expected Serial Number
   USER   Insert Module into Slot 2        (mains off, then on)
   AUTO   Power ON crate / wait for SNMP
   AUTO   Read Serial Number and compare   (safe)
   AUTO   Read safety loop bit             (safe)
   CHECK  Green LED ON?                    (safe)

3. Safety Jumper Loop Test   (x3, generated)
   USER   Reconfigure (test kit only / jumper only / restore)
   AUTO   Power cycle
   AUTO   Read safety loop bit             (safe)
   CHECK  Green LED OFF? / OFF? / ON?      (safe)

4. HV Ramp Test
   AUTO   0 -> 1000 V @ 10 V/s, hold 30 s, 1000 -> 0 V @ 10 V/s   (unsafe)
          all 8 channels, sampled to CSV + PNG, trip-monitored
          measurement is SNMP (the module's own), 1 Hz
          optionally the 34970A reads the HV divider on channels 111-118
          every 5 s and the readings are RECORDED, not converted or graded:
          the divider is 10M:500k (nominally 21.0) but measures ~44, and that
          is unresolved -- iseg_ramp_daq_enabled

5. HV Limitation Test
   USER   Select Vmax DAQ Channel           34970A slot 1, 11-20
   AUTO   Vmax Limit Adjustment             target 1.5625 V, accept 1.562-1.563
   USER   Select Imax DAQ Channel
   AUTO   Imax Limit Adjustment             target 0.15625 V, accept 0.156-0.1565
          (Imax = input 19 -> ch 119, Vmax = input 20 -> ch 120; note the
           order.  111-118 are the eight HV channel outputs)
   AUTO   HV Limit Readback                 SNMP confirms 62.5 % / 6.25 %
   (the "reaches 1.2 kV, not 1.3 kV" part of the procedure is not written yet)
```

## 7.5 Files

| File | What it is |
|---|---|
| `WIEC_Component/iseg_mpod.py` | SNMP wrapper: crate, module and channel access, grouped sampling, safe shutdown helpers |
| `WIEC_Component/operator.py` | `Step`, `StepResult`, the `Console` prompts and the `FlowRunner` engine.  Knows nothing about MPODs |
| `WIEC_Component/hv_module_test.py` | the flow itself, the AUTO actions, the outputs, and a `SimulatedMPOD` for `--dry-run` |
| `HV Module Test/hv_module_test_results.xlsx` | one row per run; columns are discovered from the flow, so adding a step does not break an old workbook |
| `HV Module Test/results/<stamp>_SN<serial>/` | ramp CSV, voltage and current PNGs, and the full JSON step record |

How to run it:

```
python3 run_wiec_component.py                             # menu, item 6
python3 run_wiec_component.py --items hv_module_test
python3 -m WIEC_Component.hv_module_test config.json --dry-run
```

`--dry-run` answers its own prompts and drives a simulated crate, so the flow
text and the pass/fail logic can be checked with no hardware attached.

## 8. Not done / follow-ups

- `iseg_control.py` and `iseg_pmod_wrapper_QC.py` in the repository root are
  near-duplicates of each other.  `WIEC_Component/iseg_mpod.py` is a third,
  focused implementation for this item; the standalone QC scripts were left
  working as they are.  Consolidating all three is worth doing later.
- The HV limitation test's pot targets are settled (5.2), but the method for
  "output reaches 1.2 kV / does not reach 1.3 kV" is still TBD, so the step
  stays informational until `iseg_limit_test_enabled` is turned on.
- **First real run reached step 39 of 40** (2026-08-20).  Everything through
  the visual inspection, the serial number check, all four power cycles and all
  three safety-jumper configurations passed with a real module.  It stopped at
  the HV ramp because of the ramp-rate bug described in 5.5 — no channel was
  ever energised.  The fix is verified on hardware (the ramp rate now takes),
  as is the whole automatic power cycle, but **the ramp itself has still never
  been run**: the next attempt is the first time high voltage is applied.
- What has been verified on the live crate is every read-only path: module discovery, serial
  number comparison (match and mismatch), module health, the safety loop bit,
  the hardware limit readings and the grouped channel sampling.  Nothing has
  been written to the crate and no channel has been energised.
- The safety loop / green LED correspondence is **confirmed** (2026-08-20):
  both the jumper and the test kit have to be present for
  `moduleSafetyLoopIsGood` to be set, which is exactly what the LED shows.  The
  AUTO reading is therefore graded against the same expectation as the
  operator's LED answer.  `iseg_grade_safety_loop` remains as an escape hatch.
