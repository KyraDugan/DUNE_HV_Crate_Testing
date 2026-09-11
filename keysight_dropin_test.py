from KeysightE36312A_interfacing import KeysightE36312A
import pyvisa
import sys
import json
import os

with open("config.json", "r") as jsonfile:
    json_data = json.load(jsonfile)
rm = pyvisa.ResourceManager('@py') 
r1 = KeysightE36312A(rm, json_data, 1)
r1.setup_hvpullup()
r1.setup_hvpullup2()
r1.setup_fanread()
