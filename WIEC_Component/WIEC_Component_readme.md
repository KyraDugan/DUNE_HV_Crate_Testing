# WIEC Component test README

This document describes the WIEC component test at its development as of Fall 2026. This development has been in the works for a while, but was taken over this fall for completion by Kyra Dugan, the SULI intern. If you have any questions that need answering, and I am no longer at BNL due to my SULI term ending, please contact me at kyramdugan@gmail.com.


## Repo Info

The WIEC component test is designed to run separately from the HV crate test. The test files as developed by the SULI intern before me were within the DUNE-HV-CRATE-TESTING folder (/home/dune-daq/DUNE-HV-Crate-Testing), and there are dependancies on other items within this folder, but honestly this is poorly named and should be kept separate.

There are many files within the root folder for the test that are completely unrelated to the WIEC QC test, but remain there due to this placement in the folder. 

The git for this folder is maintained at https://github.com/KyraDugan/DUNE_HV_Crate_Testing. 

## Design Principles

Importantly, through the development of the WIEC QC test, I have intentionally not altered files that the HV Crate test utilizes so as to not impact its dependencies. This is a goal that should be maintained if the WIEC QC test has continued development. 

The WIEC QC test should be designed without hard coding as many things as possible, except in config.json. This will allow for better customization going forward, and much easier debugging when there are inevitable hardware changes to be dealt with.

Colors should be used to convey a meaning. There is a color scheme that has been selected for this project, which can be viewed in WIEC_utils.py. Each color has a defined meaning.

The user should never be looking at the terminal without anything being conveyed to them. There should be a clear need for an input if there is one required (signified by color and >). If waiting for the program to do something, or for the harware measurement to be completed, there should be a progress bar. This removes any question on if the program has crashed or not.


## Structure

The top script is run_wiec_component.py (/home/dune-daq/DUNE-HV-Crate-Testing/run_wiec_component.py).

To see how the entire process works, please refer to the flowchart at /home/dune-daq/DUNE-HV-Crate-Testing/WIEC_Component/WIEC_QC_Flowchart.html, this will be the most useful explaination you'll have of the process. It is verbose, but well worth reading in its entirety to understand the process without needing to trace through every file yourself. It would also be good to go through the test process and see how it works on the operator side.


Within /home/dune-daq/DUNE-HV-Crate-Testing/WIEC_Component, there are many different files that are split into the following four categories:

|  Test Scripts  |  Test Helpers  |  General Helpers  |  Unused  |
|----------------|----------------|-------------------|----------|
| fan_pwm_sweep.py | analysis.py | __init__.py | hv_limit_adjust.py |
| fan_test.py |femb_checkout.py | Hardware_Instructions.odp | hv_module_report.py |
| heater_test.py | wiec_femb_chk.py | output_log.py | HV_MODULE_TEST_DEVLOG.md |
| hv_test.py | wiec_report.py | WIEC_utils.py | hv_module_test.py |
| wiec_wib_qc.py | wib_setup.py | preflight.py | iseg_mpod.py |
| | ptc_power.py | session.py | operator.py |
| | | WIEC_Component_readme.md | popups.py |
| | | | spreadsheet.py |
| | | | |


The test scripts are the tests that are actually performed on the WIEC during the QC. The test helpers are there as helpers for the test scripts themselves. The general helpers are used throughout the WIEC QC process. The unused files are there for the HV test implementation and are not used in this setup.



## Results

The results from the WIEC QC test can be found in the folder: /home/dune-daq/DUNE-HV-Crate-Testing/WIEC_Component_Results

This folder includes a spreadsheet that has an overview of each WIEC QC test, which part failed, and why it failed.

In the runs folder, each WIEC QC test has its own folder with the title "date_time_wiec_serial".

In the folder for the run, there is a plots folder with all plots created in that run. There is a raw folder with all the raw data from the run in .csv format. There is measurements.csv which has a list of each measurement done thorughout the test, with their associated values. There is a session_log.txt, which is an output of the session's log. Finally, there is summary.html which is a legible summary that can be read by the operator for quick identification of what went wrong.


