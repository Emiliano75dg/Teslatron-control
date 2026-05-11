# Teslatron controller

This Python tool is made for controlling the Teslatron system of the Q-MAT lab,
jointly operated within CNR-SPIN and the Department of Physics "E. Pancini" of
the University of Naples Federico II.

The current repository focuses on the service-oriented cryostat control layer,
with:
- real-time environmental readback
- controlled temperature, field, and gas commands
- diagnostics, GUI, and recipe support

## How to use

Install Python 3, then numpy and pyvisa. For handling and plotting the data afterwards, pandas and matplotlib are useful:
``` 
pip install numpy pyvisa pandas matplotlib
```
For PyVisa to work, you will need to install the [National Instruments VISA library](https://pyvisa.readthedocs.io/en/latest/faq/getting_nivisa.html#faq-getting-nivisa).

## Lab cryostat service

For first live checks on the Teslatron in the Q-MAT lab, use the dedicated
read-only Mercury config:

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

## Heliox backend

A dedicated Heliox backend is also available for Mercury controllers that expose the
abstract `HelioxX:HEL` interface described in the Heliox manual.

Use the read-only example config for first checks:

```text
config/heliox_readonly.example.json
```

For control sessions, use:

```text
config/heliox_control.example.json
```

Start it with:

```bash
python3 -m teslatron_services --config config/heliox_readonly.example.json --port 8766
```

Current Heliox model:
- sample temperature is controlled through the abstract `HelioxX:HEL` interface
- VTI loop and gas control remain available through the underlying Mercury iTC channels
- field control remains available through the system-global Mercury iPS
- direct sample PID/fixed-heater tuning is intentionally not exposed

The backend is implemented and locally tested; full end-to-end validation through the GUI
should still be done on the instrument in the lab before relying on it operationally.

## Maintainer

Current author and maintainer: Emiliano

Instrument reference:
- Teslatron system of the Q-MAT lab
- CNR-SPIN
- Department of Physics "E. Pancini"
- University of Naples Federico II

Copyright (c) 2024-2026 Emiliano
