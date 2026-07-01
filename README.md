# Teslatron controller

This Python tool is made for controlling the Teslatron system of the Q-MAT lab,
jointly operated within CNR-SPIN and the Department of Physics "E. Pancini" of
the University of Naples Federico II.

The current repository focuses on the service-oriented cryostat control layer,
with:

- real-time environmental readback
- controlled temperature, field, and gas commands
- diagnostics, GUI, and recipe support

## Documentation map

If you are new to the repository, start here:

- `docs/USER_MANUAL.md`: complete English user manual — GUI walkthrough, recipe
  builder, template buttons, workflows, safety rules, and troubleshooting
- `docs/MANUALE_UTENTE.md`: same manual in Italian
- `docs/user_guide.md`: guided overview of the project, startup flows, configs,
  endpoints, output files, and common workflows
- `LAB_RUNBOOK.md`: shortest safe procedure for live lab usage
- `SERVICE_ARCHITECTURE.md`: backend and API architecture for the cryostat service
- `ELECTRICAL_MEASUREMENT_ARCHITECTURE.md`: design notes for the electrical service
- `docs/electrical_measurements.md`: electrical output formats and LabVIEW integration details

Recommended reading order:

1. `README.md`
2. `docs/USER_MANUAL.md` for a guided, step-by-step introduction in English
3. `docs/MANUALE_UTENTE.md` for the same guide in Italian
4. `docs/user_guide.md` for a technical English overview
5. `LAB_RUNBOOK.md` for real hardware sessions
6. `SERVICE_ARCHITECTURE.md` only when you need implementation-level detail

## What is in this repository

The codebase currently contains two closely related service layers:

- `teslatron_services/cryostat`: FastAPI service for cryostat control, state polling,
  diagnostics, GUI, recipes, and LabVIEW-facing measurement context
- `teslatron_services/electrical`: companion service for electrical measurements
  that consumes cryostat state instead of opening Mercury controllers directly

Recommended HTTP port convention:

- `8765`: cryostat service, read-only or default local startup
- `8766`: cryostat service, control-enabled standard session
- `8767`: cryostat service, Heliox session
- `8775`: electrical measurement service

These are service ports of the local HTTP API. They are separate from
instrument-side TCP ports such as `7020` or `5025`.

The cryostat service supports three backends:

- `mock`: offline development without hardware
- `standard`: the standard cryostat configuration, with direct Mercury iTC/iPS control through VISA
- `heliox`: Heliox-specific configuration, with abstract sample control plus Mercury-based VTI/gas and iPS field control

Typical repository areas:

- `config/`: ready-to-run examples and lab configs
- `docs/`: operator notes, architecture, measurement docs, and manuals
- `tools/`: small standalone utilities
- `tests/`: regression tests for service behavior

## How to use

Install the service dependencies:

```bash
pip install -r requirements-service.txt
pip install pyvisa
```

Optional analysis tools for working with exported data afterwards:

```bash
pip install numpy pandas matplotlib plotly streamlit
```

The repository also includes small standalone utilities under `tools/`. For
example, you can inspect cryostat environment CSV logs with:

```bash
streamlit run tools/inspect_environment_log_streamlit.py
```

This launches a local Streamlit app where you can upload one or more log files,
inspect merged traces with Plotly, browse reconstructed events, and preview the
raw dataframe in a separate tab.

This offline log viewer is separate from the live cryostat control GUI. The
main live GUI continues to be served by the FastAPI cryostat service at the
service root `/`.

The live GUI also includes an `Open Log Viewer` button that opens the separate
offline Streamlit app in a new browser tab. By default it points to:

```text
http://127.0.0.1:8501/
```

The old entry point is kept only as a legacy wrapper and now prints a message
that redirects you to the Streamlit app:

```bash
python3 tools/inspect_environment_log.py
```

For PyVisa to work, you will need to install the [National Instruments VISA library](https://pyvisa.readthedocs.io/en/latest/faq/getting_nivisa.html#faq-getting-nivisa).

## One-command startup

If you want a command that works from any folder, install it once:

```bash
./install-teslatron-command
```

On Windows, the same result can be achieved with:

```powershell
python -m pip install --user -e .
```

After that, you can launch from any terminal with:

```bash
teslatron
```

If you prefer not to install anything, from the repository root you can still use:

```bash
./teslatron
```

This starts a safe mock session on:

```text
http://127.0.0.1:8765/
```

Useful variants:

```bash
./teslatron readonly
./teslatron control
./teslatron heliox
./teslatron --open-browser
```

If the project is installed as a package, the same launchers are also available as:

```bash
teslatron
teslatron readonly
teslatron control
teslatron heliox
teslatron-log
teslatron-stop
teslatron-stop --include-log-viewer
```

Command summary:

- `teslatron`: start the cryostat GUI with the default safe mock profile
- `teslatron readonly`: start the lab read-only cryostat profile on `8765`
- `teslatron control`: start the lab control-enabled cryostat profile on `8766`
- `teslatron heliox`: start the Heliox local-GUI profile on `8767`
- `teslatron-log`: start the Streamlit environment-log reader on `8501`
- `teslatron-stop`: stop the Teslatron service ports `8765`, `8766`, `8767`, and `8775`
- `teslatron-stop --include-log-viewer`: stop those ports and also the log reader on `8501`

For `teslatron-log`, keep the terminal open while Streamlit is running, then open:

```text
http://127.0.0.1:8501/
```

Stop it with `Ctrl+C` in that same terminal, or with:

```bash
teslatron-stop --include-log-viewer
```

## Quick start

### Offline development with the mock backend

Use the mock config when you want to inspect the API or GUI without any hardware:

```bash
python3 -m teslatron_services --config config/cryostat_mock.json --port 8765
```

Then open:

```text
http://127.0.0.1:8765/
```

This root page is the live cryostat GUI served by the FastAPI service from the
static frontend under `teslatron_services/cryostat/static/`.

Useful first checks:

```bash
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8765/state
curl http://127.0.0.1:8765/config
```

### Electrical service

The electrical service is a separate FastAPI process. It does not control the
Mercury hardware directly; instead, it reads cryostat context from the cryostat
service and stores that context together with electrical measurements.

Start it with the standard electrical-service port:

```bash
python3 -m teslatron_services.electrical --config config/electrical.mock.example.json --port 8775
```

The example electrical configs expect the cryostat service on `http://127.0.0.1:8765`
unless you override `electrical.cryostat.state_url` and
`electrical.cryostat.recipe_signal_url`.

## Lab cryostat service

For first live checks on the Teslatron in the Q-MAT lab, use the dedicated
read-only standard config:

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
  "magnet_temperature_K": 4.3,
  "magnet_voltage_V": 0.018,
  "safe_to_measure": true
}
```

Use explicit JSON fields such as `sample_temperature_K`, `field_T`,
`magnet_temperature_K`, and `magnet_voltage_V`. Do not
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

For a broader explanation of the available configs, endpoints, and workflows, see:

```text
docs/user_guide.md
```

## Heliox backend

A dedicated Heliox configuration is also available for Mercury controllers that expose the
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

Conceptually, the user-facing alternatives are:

- `standard`: Fisher probe or Basic probe
- `heliox`: Heliox probe only

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

## Glossary

- `cryostat service`: the HTTP service under `teslatron_services/cryostat` that owns cryostat state, GUI, recipes, and command endpoints
- `electrical service`: the HTTP service under `teslatron_services/electrical` that runs electrical measurement plans while reusing cryostat context
- `service port`: a local HTTP port used by one of the FastAPI services, such as `8765`, `8766`, `8767`, or `8775`
- `instrument port`: a TCP port exposed by a physical instrument or vendor socket interface, such as Mercury on `7020` or SCPI instruments on `5025`
- `dummy local port`: a loopback-only TCP endpoint used for offline GUI checks, such as `65001` or `65002`

## Maintainer

Current author and maintainer: Emiliano

Instrument reference:

- Teslatron system of the Q-MAT lab
- CNR-SPIN
- Department of Physics "E. Pancini"
- University of Naples Federico II

Copyright (c) 2024-2026 Emiliano
