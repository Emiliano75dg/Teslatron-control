# Teslatron controller

This Python tool is made for controlling the Teslatron system at the University of Geneva Department of Quantum Materials Physics.

The aim of the tool is to extend the capabilities of the instrument beyond the existing labview program by:
- allowing simultaneous measurement from many voltmeters
- allowing temperature sweeps by sweeping heater power directly
- giving the ability to write more complicated measurement programs through a script

## How to use

Currently the best way to control the Teslatron is to write a measurement script as a .py or (preferably) .ipynb Jupyter notebook file.

Install Python 3, then numpy, pyvisa, and notebook. For handling and plotting the data afterwards, pandas and matplotlib are useful:
```
pip install numpy pyvisa notebook pandas matplotlib
```
For PyVisa to work, you will need to install the [National Instruments VISA library](https://pyvisa.readthedocs.io/en/latest/faq/getting_nivisa.html#faq-getting-nivisa).

Clone this repository, and see the example_measurement_script.ipynb to see how one can write and execute a measurement script on the Teslatron system.

## Lab cryostat service

For first live checks in the lab, use the dedicated read-only Mercury config:

```text
config/cryostat_lab_readonly.json
```

For live control sessions, use:

```text
config/cryostat_lab_control.json
```

Start the service with:

```bash
python3 -m teslatron_services --config config/cryostat_lab_readonly.json --port 8765
```

Then query only read-only endpoints:

```bash
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8765/state
curl http://127.0.0.1:8765/diagnostics/readings
```

Important: do not keep the same Mercury controller open in LabVIEW and Python at the same
time. During live testing on 2026-05-11, the iPS at
`TCPIP::172.31.109.116::7020::SOCKET` reset Python connections while the LabVIEW VI still
held the session, and replied normally as soon as the VI disconnected.

For the recommended lab workflow, command order, and safety notes, see:

```text
LAB_RUNBOOK.md
```

Copyright (c) 2024 Graham Kimbell
