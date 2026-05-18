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

Install the service dependencies:

```bash
pip install -r requirements-service.txt
pip install pyvisa
```

Optional analysis tools for working with exported data afterwards:

```bash
pip install numpy pandas matplotlib
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

Saved recipes are kept under the configured `recipe_dir`. The `/recipes/save`
endpoint now defaults to safe behavior and will not overwrite an existing
recipe unless `overwrite=true` is provided explicitly in the JSON payload.

### LabVIEW integration for electrical measurements

The cryostat service now exposes a lightweight endpoint intended for LabVIEW or
other external acquisition software:

```text
GET /measurement-context
```

Recommended response shape:

```json
{
  "timestamp_unix_s": 1710000000.123,
  "timestamp_iso": "2024-03-09T10:00:00.123Z",
  "sample_temperature_K": 4.21,
  "field_T": 1.5,
  "safe_to_measure": true
}
```

Use explicit JSON fields such as `sample_temperature_K` and `field_T`. Do not
use anonymous arrays like `[T, B]`, because they are fragile across LabVIEW,
Python, and future API revisions.

Recipe-based external measurement handshakes are also available through:

```text
GET  /external-measurements/pending
POST /external-measurements/complete
POST /recipes/signal
```

Recipes can include `external_measurement` steps with:

- `mode: "point"` to pause at a stable point and wait for LabVIEW
- `mode: "start"` to start a continuous acquisition before a ramp
- `mode: "stop"` to stop the continuous acquisition after a ramp

For slower acquisitions, polling `GET /measurement-context` at about 1-5 Hz is
adequate. For faster acquisitions, prefer timestamp-based offline merge of the
electrical data and the cryostat context. See
`docs/electrical_measurements.md` for more detail.

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

For local GUI-only checks without Heliox hardware, use:

```text
config/heliox_local_gui.example.json
```

This offline config is intentionally writable so the GUI controls remain interactive.
Commands still stay safe because the ITC/IPS addresses point to loopback-only dummy endpoints.

For control sessions, use:

```text
config/heliox_control.example.json
```

Start it with:

```bash
python3 -m teslatron_services --config config/heliox_readonly.example.json --port 8767
```

For the offline GUI-only config:

```bash
python3 -m teslatron_services --config config/heliox_local_gui.example.json --port 8767
```

Current Heliox model:
- sample temperature is controlled through the abstract `HelioxX:HEL` interface
- VTI loop and gas control remain available through the underlying Mercury iTC channels
- field control remains available through the system-global Mercury iPS
- direct sample PID/fixed-heater tuning is intentionally not exposed

The backend is implemented and locally tested; full end-to-end validation through the GUI
should still be done on the instrument in the lab before relying on it operationally.

## Electrical measurement outputs

Simple electrical runs now produce two complementary files under `data/electrical/YYYY-MM-DD/`:

- JSONL event log for machine-oriented replay and debugging
- per-run CSV for direct scientific analysis

The tabular CSV is saved in a dedicated run directory:

```text
data/electrical/YYYY-MM-DD/<run_id>/<run_id>_electrical.csv
```

It includes one row per electrical measurement with explicit columns for:

- run metadata: `run_id`, `plan_id`, `instrument`
- time axes: `timestamp_unix_s`, `timestamp_iso`, `time_relative_s`
- cryostat context: `sample_temperature_K`, `field_T`, `safe_to_measure`
- optional cryostat extras when available: `vti_temperature_K`, `pressure_mbar`, `cryostat_timestamp`
- flattened electrical payload fields from the instrument measurement

`timestamp_iso` is written in UTC with a trailing `Z`. `time_relative_s` is computed from
`time.monotonic()` at run start, so it is stable even if the system clock changes.

For a fuller description and an example row, see:

```text
docs/electrical_measurements.md
```

## Maintainer

Current author and maintainer: Emiliano

Instrument reference:
- Teslatron system of the Q-MAT lab
- CNR-SPIN
- Department of Physics "E. Pancini"
- University of Naples Federico II

Copyright (c) 2024-2026 Emiliano
