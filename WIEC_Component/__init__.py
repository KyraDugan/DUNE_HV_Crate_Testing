"""WIEC_Component -- the DUNE HV crate production test, split into items.

Each test phase that used to live inside LDOmeasure in dune_hv_crate_test.py
is now its own module with a run(session) function:

    fan_test        Fan supply V/I and the 6 RD signals at nominal voltage
    fan_pwm_sweep   Fan voltage sweep, PWM frequency curves
    heater_test     Heating element resistance and RTD temperature rise
    hv_test         High voltage leakage / RC time constant, 8 phases
    ptc_power       WIEC PTC 48 V power up / down (fans confirmed first)
    hv_module_test  Operator-guided QC of one iseg HV module in an MPOD slot
                    (see HV_MODULE_TEST_DEVLOG.md and
                    "HV Module Test/HV_Module_Test_Design.md")

They share a TestSession (session.py) that owns the config, the instrument
handles, the results directory and the spreadsheet row.  Drive them from the
top script:

    python3 run_wiec_component.py

or run one item on its own:

    python3 -m WIEC_Component.fan_test config.json my_test_name
"""

import os
import sys

# The instrument drivers (keysight_daq970a, rigol_dp832a, ...) live in the
# repository root, and caen_r8033dm.py loads its .so relative to the working
# directory, so make sure the root is importable however we were started.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
