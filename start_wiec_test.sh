#!/bin/bash
# Starts the WIEC component test from the repository root
# (run_wiec_component.py loads the CAEN library from the working directory).
cd /home/dune-daq/DUNE-HV-Crate-Testing || exit 1

# Keep this script alive through Ctrl+C so the window stays open.
# Python still gets the Ctrl+C and shuts down normally.
trap ':' INT

if pgrep -f "run_wiec_component.py" > /dev/null; then
    echo "The WIEC test is already running in another window."
    echo "Two copies would fight over the same instruments, so this one will not start."
else
    /usr/bin/python3 run_wiec_component.py
fi

echo
echo "WIEC test ended. Type 'exit' to close this window."
exec bash

