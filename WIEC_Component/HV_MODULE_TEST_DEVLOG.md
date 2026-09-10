# HV Module Test — development log

How the `hv_module_test` item came to be, in the order it happened, including
the things that turned out to be wrong.  Written so the work can be picked up
again after the PC is shut down and nobody remembers the conversation.

- Session date: **2026-08-20**
- Companion document: **`HV Module Test/HV_Module_Test_Design.md`** — that one
  describes the *current design*.  This one describes *how it got there* and
  **why several obvious-looking approaches do not work**.  Read that one to
  understand the code; read this one before changing it.

---

## 1. Where everything is

### New code

| File | Lines | What it is |
|---|---|---|
| `WIEC_Component/iseg_mpod.py` | 569 | SNMP driver for the WIENER MPOD crate and its iseg modules |
| `WIEC_Component/operator.py` | 339 | `Step` / `StepResult` / `Console` / `FlowRunner`. Knows nothing about MPODs |
| `WIEC_Component/hv_module_test.py` | ~1290 | The flow, the AUTO actions, the output files, and simulators for `--dry-run` |
| `WIEC_Component/hv_limit_adjust.py` | ~390 | Guided Vmax / Imax potentiometer adjustment, watched on the 34970A over GPIB |
| `WIEC_Component/hv_module_report.py` | ~300 | Turns a results directory into one self-contained HTML report |

### Existing files that were changed

| File | Change |
|---|---|
| `WIEC_Component/session.py` | lazy `mpod` property; MPOD channels added to `emergency_shutoff()` and `shutdown_only()` |
| `WIEC_Component/preflight.py` | ping the MPOD; check `snmpget`/`snmpset`/`snmpwalk` exist |
| `run_wiec_component.py` | import the item, add it to `EXTRA_ITEMS` (menu entry 6) |
| `config.json` | 27 `iseg_*` keys |
| `iseg_pmod_wrapper_QC.py` | bug fix: `turn_off_crate` called `self.snmp_int`, which does not exist |
| `demo.py` | now the bench front-end for the limit adjustment; the old channel readout is still there under `--read` |

### Output

```
HV Module Test/HV_Module_Test_Design.md              the design record
HV Module Test/hv_module_test_results.xlsx           one row per run
HV Module Test/results/<stamp>_SN<serial>/
    report.html                       self-contained, images embedded
    hv_module_test_SN*_results.csv    one row per step
    hv_ramp_SN*.csv                   per-channel ramp samples
    hv_ramp_daq_SN*.csv               34970A readings, when wired
    hv_module_test_SN*.json           everything, machine readable
    *.png                             the curves
HV Module Test/dry-run/<stamp>_SN<serial>/           --dry-run output only
```

**`--dry-run` writes to `dry-run/`, never to `results/`.**  They shared a
directory once, and a `rm -rf` tidying up simulated output took a real run's
data with it — 1696 samples from the first successful HV ramp, unrecoverable.
Simulated and measured data do not belong in the same tree.

The HTML report is built automatically when a run finishes.  Rebuild an old
one after changing the template:

```bash
python3 -m WIEC_Component.hv_module_report                 # newest run
python3 -m WIEC_Component.hv_module_report <results dir>
python3 -m WIEC_Component.hv_module_report --all
```

Nothing is written to `DUNE_HV_Crate_Production_Test.xlsx`.

### How to run

```bash
python3 run_wiec_component.py                                  # menu, item 6
python3 run_wiec_component.py --items hv_module_test
python3 -m WIEC_Component.hv_module_test config.json --dry-run  # no hardware
```

`--dry-run` answers its own prompts and drives a simulated crate.  Use it after
any change to the flow — it exercises all 36 steps in about two minutes and exits
non-zero if the verdict is FAIL.

---

## 2. What was asked for

A test-flow proposal for qualifying one iseg HV module in MPOD slot 2, with
every step classified as one of three kinds:

- **`[AUTO]`** software does it, reads it, judges it
- **`[USER]`** the operator does something with their hands
- **`[CHECK]`** the operator looks at the hardware and answers PASS / FAIL

The flow: visual inspection → install test kit and jumper → insert into slot 2
→ serial number check → green LED → three safety-jumper configurations → HV
ramp 0→1000 V @ 10 V/s, hold 30 s, back down → HV limitation test (the Vmax/Imax potentiometer adjustment, added later in the session).

The request was that it become a new item in `run_wiec_component.py`, and that
the interface show one large prompt per manual step rather than a wall of text,
so the operator does not need the SOP open.

### The five decisions the user settled

| # | Question | Answer |
|---|---|---|
| 1 | Ramp all 8 channels together, or one at a time? | Together — one test in total, but per-channel data recorded |
| 2 | How is the crate powered down for module insertion? | **Changed during the session — see §5.1.** Final answer: the PC does it over SNMP |
| 3 | Where do the results go? | A separate workbook of its own |
| 4 | Serial number | Operator types it first, software reads and compares, mismatch warns loudly |
| 5 | On failure | Safe → record, warn, ask whether to continue. Unsafe → ramp down, power off, abort |
| 6 | Operator confirmation | A USER step must never advance on a bare Enter — see §5.2 |

---

## 3. The design that came out of it

**The flow is data, not code.**  `build_flow(ctx)` returns a list of `Step`
objects and `FlowRunner` walks it.  This is why the three safety-jumper
configurations are generated from `JUMPER_CONFIGS` instead of copy-pasted, and
why a GUI can reuse the flow by subclassing `Console` and overriding four
methods — the flow itself would not change.

**Two failure severities, and the difference matters.**

| Severity | Steps | On failure |
|---|---|---|
| `safe` | visual, SN, LED checks — module unpowered or at low voltage | record FAIL, warn, ask whether to carry on, overall verdict stays FAIL |
| `unsafe` | HV ramp, HV limit, "confirm crate is off", any trip or comms loss | ramp to 0, channels off, crate off, red warning, abort |

`safe_state(ctx)` runs from a `finally`, so Ctrl-C, an unhandled exception and
an unsafe failure all take the same path out.  `TestSession.emergency_shutoff()
` was extended too — before this work it knew nothing about the ISEG, so a
Ctrl-C during a ramp would have left the MPOD energised.  That was a real gap,
not a hypothetical one.

---

## 4. What the crate actually does (measured, not assumed)

Every one of these was found by probing `169.254.4.31`, and several contradict
what the MIB text suggests.

| Finding | Consequence |
|---|---|
| `moduleSerialNumber` is **not implemented** | the serial number is parsed out of `moduleDescription.maN` |
| `outputHardwareLimitVoltage/Current` (per channel) are **not implemented** | the module-level `moduleHardwareLimitVoltage.maN` is used instead, and it reads in **percent of full scale**, not volts |
| `moduleRampSpeedVoltage` is a **percent** | 10 V/s is 0.5 % on a 2 kV module |
| `moduleSafetyLoopIsGood(10)` follows the **jumper** | pulling the jumper also latches `moduleIsEventActive(11)` — see §5.0 |
| `moduleIsGood(12)` equals the **green LED** | needs jumper *and* test kit, so the operator's visual answer gets an automatic cross-check |
| slot 2's module is **negative polarity** (`outputConfigMaxSenseVoltage = -2000 V`) | polarity is detected from that sign, never hard-coded |
| setpoints read back **positive** on that negative module | `set_voltage()` writes magnitudes; only readbacks are signed |
| `outputSwitch = clearEvents(10)` | a tripped channel will not ramp until its events are cleared |
| three `snmpwalk`s beat 24 `snmpget`s | a full 8-channel sample takes **0.09 s**, which is what makes 1 Hz sampling through a ramp practical |

The voltage/current potentiometer percentages line up exactly with the
limitation-test spec, which is how the typo in it was confirmed:

| Pot | Control voltage | Percent | On this module |
|---|---|---|---|
| voltage | 1.5625 V | 62.5 % | 1.25 kV of 2 kV |
| current | 0.15625 V | 6.25 % | **0.25 mA** of 4 mA — the proposal's "4 mA" was a typo |

---

## 5. Things that were wrong, and how they were found

**This is the part worth reading before changing anything.**  Each of these
looked correct, was implemented, and then failed against real hardware.

### 5.0 The safety jumper bit — two wrong guesses and a mis-attributed reading

`moduleStatus` bit 10 `moduleSafetyLoopIsGood` looked like a free automatic
check of the safety jumper.  Working out what it actually tracks took three
attempts and cost two real runs.  The measured truth, all three
configurations on ma2:

| jumper | test kit | bit 8 | bit 10 loop | bit 11 event | bit 12 good | green LED |
|---|---|---|---|---|---|---|
| in | in | set | **set** | clear | **set** | **ON** |
| in | out | clear | **set** | clear | clear | OFF |
| out | in | set | **CLEAR** | **SET** | clear | OFF |

```
bit 10 = the JUMPER          -- pulling it also latches moduleIsEventActive(11)
bit 12 = jumper AND test kit -- exactly the green LED
```

The three attempts:

1. **"the loop follows the LED"** — the original guess, never measured.
   Failed the jumper-only step: LED off but the loop stayed closed, because
   the jumper was still in.
2. **"then it follows the jumper"** — correct, as it turns out, but abandoned
   for the wrong reason.  A reading taken in what was believed to be the
   test-kit-only configuration came back identical to the normal
   configuration, which seemed to prove the bit responded to nothing.
3. **"the bit is always set"** — wrong.  That reading had been taken with the
   jumper still fitted; **the physical state was never verified against the
   step the operator was actually on.**  Grading "always closed" then failed
   the test-kit-only step in the opposite direction.

The lesson is not about this bit.  It is that a reading is worthless without
knowing what was physically fitted when it was taken, and that three
configurations were needed before any of them could be interpreted.  Reading
1 and reading 3 are the *same six bits*; only the hardware state told them
apart, and that state was assumed rather than checked.

The configurations now record only what is fitted — `jumper_in`,
`test_kit_in` — and every expectation is derived:

```python
loop_closed = config["jumper_in"]
led_on      = config["jumper_in"] and config["test_kit_in"]
module_good = led_on
```

One place to be wrong instead of three.

### 5.1 "The AC mains must be switched off by hand" — wrong

The MIB says `sysMainSwitch` switches "the complete crate ON or OFF", but the
controller stays reachable afterwards, which read like proof that mains was
still present and that a person had to throw the rear-panel switch.  The flow
was built that way, with the software verifying by **waiting for SNMP to go
silent**.

It never goes silent.  The controller runs on standby with the bay dead, so
that step waited for an event that cannot happen, and the operator was told
they had not done something they had in fact done.

Measured behaviour, `sysMainSwitch` alone:

```
OFF  fans stop, sysStatus = inputFailure(3), moduleNumber 0, outputNumber 0
ON   t= 2.1s  2 modules  16 outputs   slot 2 absent
     t=10.5s  4 modules  32 outputs   slot 2 absent
     t=12.6s  5 modules  40 outputs   slot 2 PRESENT
```

So the PC can power cycle the crate on its own — `iseg_power_cycle_mode` now
defaults to `"snmp"`, and the eight manual power steps are gone.  The check
became **positive evidence** instead of an absence: `module_bay_powered()`
accepts either "crate not answering at all" or "controller answering,
`sysMainSwitch` off, **0 modules and 0 outputs enumerated**".  A module cannot
be enumerated over the internal bus without bay power.

Two things were *not* simplified away with it:

- `Confirm Crate Is Off` still runs in both modes.  An `snmp` mode that fires
  `sysMainSwitch` and moves on would be *worse* than the manual one, because
  nobody is checking any more.
- The SNMP table clears in **0.1 s** while the fans take seconds to stop, so a
  settle wait was added (`iseg_power_off_settle_s`, 10 s).  That 0.1 s figure
  was too flattering to trust.

### 5.2 A bare Enter should not advance a manual step

The operator has just been asked to do something physical, and Enter is the
reflex used to skip past prompts.  USER steps now require a typed `y`; `q`
stops the test cleanly from any prompt, is recorded as the reason the run
ended, and still runs the full safe-state shutdown.  The "carry on after a safe
failure?" question has no default either.

### 5.3 Writing the ramp rate per channel — wrong

`outputVoltageRiseRate.uNNN` is read-write in the MIB and the existing
standalone scripts write it, so the item did too.  **snmpset returns success
and the value never changes.**  On the first real run, a commanded 10 V/s read
back as 100 V/s — exactly the 5 % the module was already set to.

The MIB says why, in the description of the module-level OID: *"iseg HV modules
have the same ramp speed value for all channels"*.  A per-channel OID cannot
express a module-wide setting, so the controller accepts the write and discards
it.  Write `moduleRampSpeedVoltage.maN` as a percentage instead.  Verified:

```
set  10 V/s -> wrote 0.500 % | module reads 0.500 % | u200  10.00 V/s  u207  10.00 V/s
set 100 V/s -> wrote 5.000 % | module reads 5.000 % | u200 100.00 V/s  u207 100.00 V/s
```

0.5 % is not clamped by this module.

**The readback guard caught this**, refused to ramp and aborted.  Without it
the module would have ramped to 1000 V at 100 V/s instead of 10 V/s.  Keep that
guard.

### 5.4 Waiting for `moduleNumber > 0` — not enough

The bay enumerates progressively and slot 2 comes up **behind** other slots
(2 modules at t=2 s, slot 2 only at t=12.6 s).  Waiting for a non-zero count
let the flow continue ten seconds early.  Power-up now waits for
`moduleDescription.ma<slot>` specifically, then settles
`iseg_power_on_settle_s` (5 s) before touching the module's settings.

The same counts run **downwards** (5 → 2 → 0) during power-down, which is easy
to misread as a crate coming up.  It was misread once during this session.

### 5.5 Retrying permanent SNMP errors

With the bay unpowered the channels do not exist and `snmpset` answers
`noAccess`.  The generic retry logic tried each of 8 channels four times with a
sleep between, turning a doomed call into **25 seconds of silence** that looked
like a hang.  Permanent errors (`noAccess`, `noSuchName`, `notWritable`, …) are
now never retried, and `outputs_off` skips entirely when the bay is already
dead.

### 5.6 One channel used for two potentiometers

`hv_module_test` was fixed to ask for a channel per stage, but
`hv_limit_adjust.run_adjustment()` — the path `demo.py` uses — still took a
single channel and passed it to both.  The tester typed **19** (Imax) at the
one prompt, and the Vmax stage happily watched the Imax pot:

```
 STEP 1 of 2 -- Vmax limit adjustment
| Watching       : DAQ channel 119                                 |
  17:11:09  Vmax    1.551093 V   target 1.5625 V   diff -0.011407 V   IN RANGE
    Vmax is good now -- press Enter to accept
```

Two things made this worse than an ordinary mix-up:

- **1.551 V sat inside the Vmax window**, which was 1.55 – 1.57 at the time.
  Watching the wrong pot did not fail, it *passed*.  The window was tightened
  to 1.562 – 1.563 straight afterwards, which closes this particular hole --
  but the lesson is the mix-up, not the window.
- The prompt just said "DAQ channel", so there was nothing to notice.

There is now no single-channel form of `run_adjustment` at all, the prompt
names the pot it is asking about, and the per-stage summary prints which
channel it watched.  Fixing the item but not the module the item shares with
the bench tool is the general shape of this mistake — **when a call signature
carries a value that differs per stage, check every caller.**

### 5.7 A dry-run console that answers every question the same way

`AutoConsole.ask_text` returned the serial number for *every* typed field, so
when a second one appeared (the DAQ channel) the dry run fed `DRYRUN123` into
it and the step failed.  Prompts now carry their `input_key` through to the
console, and the dry run answers each one appropriately.  A stub that answers
plausibly for one question is not a stub that answers plausibly for two.

### 5.8 Silent waits look like crashes

Three separate times a deliberate pause was reported as "it stopped".  Every
wait now prints a live line showing elapsed time and what the crate is
currently reporting.  **If you add a wait, give it a countdown.**

---

## 6. State at the end of the session

**Verified on hardware** — every read path (module discovery, serial number
match *and* mismatch, module health, safety loop, hardware limits, grouped
sampling at 0.09 s), the full automatic power cycle in both directions with
real timings, and the ramp-rate write after the fix.

**Verified in `--dry-run`** — all 36 steps, PASS, exit 0.

**Verified on the 34970A** — `demo.py --read` and the monitor loop both read
the real instrument (`HEWLETT-PACKARD,34970A`), print at 0.5 s, colour
correctly and time out as designed.

**Verified in a real run** — the first attempt reached **step 39 of the
then-40** with a real module in the crate: visual inspection, serial number,
all four power cycles and all three safety-jumper configurations passed.  It
stopped at the HV ramp on the bug in §5.3.

**Never yet done: the HV ramp itself.**  No channel of this module has been
energised by this item.  The next run is the first time high voltage is
applied — 0 → 1000 V at 10 V/s, 30 s hold, back down, all 8 channels, sampled
at 1 Hz with trip monitoring and automatic ramp-down on any fault.

### The limit adjustment (flow section 5)

The tester turns a potentiometer while the **34970A on GPIB** (board 0,
address 9 — *not* the LAN DAQ970A in `keysight970a`) watches the control
voltage it produces.  Readings print twice a second, yellow with a `turn UP` /
`turn DOWN` hint while outside the window, green once inside:

```
  16:52:00  Vmax    1.538086 V   target 1.5625 V   diff -0.024414 V   turn UP
  16:52:01  Vmax    1.559448 V   target 1.5625 V   diff -0.003052 V   turn UP
  16:52:02  Vmax    1.562500 V   target 1.5625 V   diff +0.000000 V   IN RANGE
    Vmax is good now -- press Enter to accept
```

| Stage | Target | Accept window | Meaning |
|---|---|---|---|
| Vmax | 1.5625 V | 1.562 – 1.563 V | 62.5 % = 1.25 kV of 2 kV |
| Imax | 0.15625 V | 0.156 – 0.1565 V | 6.25 % = 0.25 mA of 4 mA |

Both windows are half a millivolt either side of the target.  The 34970A
resolves that comfortably; whether a hand on a screwdriver does is the
tester's problem, and the reason the reading is printed twice a second with a
turn-up / turn-down hint.

Notes on the implementation:

- **`STABLE_SAMPLES = 3`.**  The voltage sweeps through the window while the
  pot is being turned, so a single lucky sample must not end the stage.
- **The stage is an AUTO step even though a person turns the screwdriver** —
  the verdict comes from the instrument, not from the operator's opinion.
- **`MAX_STAGE_SECONDS = 600`.**  A monitor loop with no upper bound is a hang
  waiting to happen; a buggy simulator span one for as long as it was allowed
  to during development.
- **The pop-up windows are not written yet.**  Everything goes through
  `notify_tester()`, which prints a box and takes an optional `popup(stage,
  channel)` callable — add the window there, nothing else changes.
- `demo.py` is a thin front-end over `hv_limit_adjust`, not a second copy, so
  the bench tool and the test item cannot drift apart.
- **Vmax and Imax get a channel each.**  The first version asked for one
  channel and used it for both stages, which would have measured the same
  point twice.

### Which DAQ channels the potentiometers are on

Confirmed by the tester, 2026-08-21:

| Input channel | 34970A channel | Potentiometer |
|---|---|---|
| **19** | 119 | **Imax** |
| **20** | 120 | **Vmax** |

**Note the order** — 19 is the *current* limit and 20 is the *voltage* limit,
which is the opposite of the way the pair usually gets written down.  Before
this was confirmed, the code deliberately kept both defaults `null` and made a
missing channel an error rather than guessing; the two free channels were
obvious (111–118 are the HV channel outputs), but which was which was not, and
a wrong channel produces a confident wrong reading rather than a failure.

Now in `config.json` as `iseg_limit_imax_daq_channel: 19` and
`iseg_limit_vmax_daq_channel: 20`, so the operator can just press Enter.

**Not yet verified with the module powered.**  Read with the bay off, both
channels sit at about 1.55 V — nearly identical, so those numbers mean nothing
about the pot settings.  Worth reading them once with the crate on and
comparing against the SNMP percentages (`moduleHardwareLimitVoltage.ma2` /
`...Current.ma2`) to confirm the 2.5 V = 100 % scaling end to end.  The attempt
to do that failed only because the lab NIC `enp2s0` went down.

### First successful HV ramp — 2026-08-25

The ramp ran end to end on a real module for the first time:

| | |
|---|---|
| up | 101.3 s (100 s expected) |
| hold | 30.9 s |
| down | 100.5 s |
| measured rate, all 8 channels | **9.934 – 9.968 V/s** (commanded 10) |
| trips | none |
| verdict | PASS |

1696 CSV rows, 8 channels x (93 up + 28 hold + 91 down).  Channel-to-channel
spread under 0.03 V/s.  Output in
`HV Module Test/results/20260825151214_SN7200489/`.

The same run failed both limit-adjustment steps with
`ModuleNotFoundError: No module named 'gpib'` — it had been started with the
**project venv**, which does not carry linux-gpib.  Preflight now checks for
the module and says so up front instead of letting a run get 35 steps in, and
`Context.daq()` raises a message naming `/usr/bin/python3`.

### Which measurement path is which

Worth being explicit, because the two are easy to confuse:

| What | Path | Wiring needed |
|---|---|---|
| HV ramp voltage/current | **SNMP** `outputMeasurementSenseVoltage` / `...Current` / `outputStatus` | none — the module reports itself |
| Vmax / Imax pots | **GPIB** to the 34970A, channels 119 / 120 | pot outputs to the DAQ |

So the ramp curve is the module's **own** measurement.  That is enough to
qualify ramp behaviour, channel consistency and trip-free operation, but it
**cannot detect a mis-calibrated module** — setpoint and readback come from the
same board, so one reporting 1000 V while outputting 900 V looks perfect.

The old `iseg_HV_electrical_test.py` did it the other way: HV outputs through a
divider into 34970A channels **111–118** (`u200`→111 … `u207`→118), read at
about 1.15 V for a 50 V setpoint.

**Done 2026-08-25: the DAQ now cross-checks the ramp.**  Enable with
`iseg_ramp_daq_enabled` (off by default -- it needs the divider wired).

- HV outputs go through the divider into 34970A channels **111-118**
  (`u200`->111 ... `u207`->118), the same wiring as the old electrical test.
- A DAQ scan of eight channels takes **1.70 s** (2.22 s read one at a time)
  against 0.09 s for an SNMP sample, so the DAQ runs on its own interval
  (`iseg_ramp_daq_interval_s`, 5 s) instead of every time round the 1 Hz loop.
  A 100 s ramp gives ~20 comparison points per channel.  The 1.7 s gap in SNMP
  sampling is acceptable: overcurrent protection is the module's own hardware
  (`outputTripActionMaxCurrent = channelOff`, 500 ms), our polling only records.
- Outputs: `hv_ramp_daq_SN*.csv`, the DAQ points overlaid as markers on
  `hv_ramp_voltage.png`, and `hv_ramp_daq_error.png` -- percentage difference
  against the module's reported voltage, with the tolerance lines drawn.  The
  residual plot is where a divider or channel problem actually shows up; the
  overlay alone hides everything under the curve.
- Graded against `iseg_ramp_daq_tolerance_percent` (5 %), and the per-channel
  **fitted** ratio is recorded next to the configured one.

**The divider is 10M : 500k — nominally 21.0 — and the measurements say
~44.**  A factor of 2.1 that is not explained yet.  What the arithmetic gives:

| assumption | ratio |
|---|---|
| 10M : 500k | 21.0 |
| 10M : 500k, 34970A's 10 MΩ input loading the bottom leg | 22.0 |
| 20M : 500k | 41.0 |
| **20M : 500k with the meter loading the bottom leg** | **43.0** |
| **measured, July IV runs, 50–500 V** | **43.5 – 45.0** |

Only the last assumption fits, which would mean two 10M resistors in series in
the top leg.  Unconfirmed.

**So nothing is converted and nothing is graded** (decided 2026-08-25): the run
records the raw meter volts, the module's own reading, and the ratio the pair
implies, and plots them.  Deriving a voltage through either candidate ratio
would bake an unresolved disagreement into the data; grading against one would
just produce a column of failures.  Look at the numbers first, decide after.

Also unexplained: the implied ratio droops about 4 % between a 50 V and a
2000 V setpoint.  The 34970A's input impedance is 10 MΩ on ranges above 10 V
but >10 GΩ below when `INP:IMP:AUTO` is on, which changes the loading partway
up the ramp — worth checking, though the sign of that effect is wrong for what
is observed.

Per-channel spread is 3.4 %, so whatever the ratio turns out to be it has to
be per channel, not one global number.

### Still open

- The rest of the limitation procedure — "output reaches 1.2 kV, does not
  reach 1.3 kV" — is still not written, so it is not attempted.
- Both pots on the module currently in the crate read **~102 %**, i.e. wide
  open.  That is what the adjustment step is for.
- `iseg_control.py` and `iseg_pmod_wrapper_QC.py` in the repository root are
  near-duplicates of each other, and `WIEC_Component/iseg_mpod.py` is a third,
  focused implementation.  The standalone QC scripts were deliberately left
  alone; consolidating all three is worth doing later.

### Working habits worth keeping

- **Check `pgrep -af run_wiec_component` before touching the crate.**  During
  this session a probe script power-cycled the crate while an operator's run
  was in progress.  Nothing was damaged, but the run was disrupted, and if
  someone had had their hands on a module it would have been worse.
- Prefer `--dry-run` for anything that does not need the crate.
- When the MIB and the hardware disagree, the hardware is right.  Four of the
  five mistakes in §5 came from trusting a MIB description or an existing
  script instead of measuring.
