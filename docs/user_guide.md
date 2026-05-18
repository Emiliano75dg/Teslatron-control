# Teslatron User Guide

This guide is the main operator-facing overview of the repository. It connects
the quick commands in `README.md`, the live-safety notes in `LAB_RUNBOOK.md`,
and the deeper implementation details in `SERVICE_ARCHITECTURE.md`.

## 1. What this project does

The repository provides a service-oriented control layer for the Teslatron
cryostat and related electrical measurements.

The main idea is:

- one service owns the cryostat hardware and its state
- other tools read cryostat state from that service instead of opening the
  Mercury controllers directly
- recipes, GUI actions, and external acquisitions all meet through a stable HTTP API

This keeps live control centralized and reduces conflicts between different
clients.

## 2. Main components

### Cryostat service

The cryostat service lives under `teslatron_services/cryostat` and is the main
entry point for:

- environmental polling
- GUI readback
- temperature, field, and gas commands
- diagnostics and raw query helpers
- recipe execution
- measurement-context export for LabVIEW or other external software

### Electrical service

The electrical service lives under `teslatron_services/electrical`. It is meant
to run electrical measurement plans while reusing the latest cryostat context.

It should not open the Mercury iTC or iPS directly. Instead, it polls the
cryostat service and stores the latest environment snapshot together with the
electrical data.

## 3. Supported cryostat backends

The cryostat service currently supports three backends.

From the user point of view, though, the two real cryostat configurations are:

- `standard`: for Fisher probe or Basic probe
- `heliox`: for Heliox probe only

The canonical backend names are now:

- `standard`
- `heliox`

For backward compatibility, older configs that still use `backend: "mercury"`
are normalized automatically to `standard`.

### `mock`

Use `mock` when:

- you want to test the GUI locally
- you want to inspect the API
- you are developing without hardware access

This is the safest startup mode and the best first step for repository
familiarization.

### `standard`

Use `standard` when:

- you want direct iTC and iPS control through VISA
- you are working with the standard cryostat configuration
- you are using the Fisher probe or the Basic probe
- you need real cryostat readback or real commands

This backend uses the configured VISA addresses and channel mappings from the
selected config.

### `heliox`

Use `heliox` when:

- the setup exposes the abstract `HelioxX:HEL` sample-control interface
- sample control should follow the Heliox model instead of the standard one
- you are using the Heliox probe

In the current design:

- sample control uses `HelioxX:HEL`
- VTI and gas control still come from the Mercury iTC side
- field control still comes from the Mercury iPS side

## 4. Repository layout

Important folders and files:

- `README.md`: short project entry point
- `LAB_RUNBOOK.md`: safe live-lab workflow
- `SERVICE_ARCHITECTURE.md`: cryostat architecture and detailed endpoint notes
- `ELECTRICAL_MEASUREMENT_ARCHITECTURE.md`: electrical-service design
- `config/`: service configs and examples
- `docs/manuals/`: vendor manuals and local configuration captures
- `tools/`: helper scripts
- `tests/`: regression tests

## 5. Installation

Install the Python dependencies:

```bash
pip install -r requirements-service.txt
pip install pyvisa
```

Optional analysis tools for exported data:

```bash
pip install numpy pandas matplotlib
```

Important for live VISA access:

- PyVISA alone is not enough
- the National Instruments VISA runtime must also be installed on the machine

## 6. First startup paths

### A. Offline mock startup

This is the recommended first run:

```bash
python3 -m teslatron_services --config config/cryostat_mock.json --port 8765
```

Open:

```text
http://127.0.0.1:8765/
```

Useful checks:

```bash
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8765/state
curl http://127.0.0.1:8765/config
curl http://127.0.0.1:8765/diagnostics
```

### B. Live read-only standard startup

Use this when connecting to the real lab hardware for inspection only:

```bash
python3 -m teslatron_services --config config/cryostat_lab_readonly.json --port 8765
```

In this mode, readback works but command endpoints are blocked.

### C. Live control standard startup

Use this only when you are ready to send commands:

```bash
python3 -m teslatron_services --config config/cryostat_lab_control.json --port 8766
```

### D. Heliox startup

Use the Heliox example config on a separate port:

```bash
python3 -m teslatron_services --config config/heliox_readonly.example.json --port 8767
```

For GUI-only frontend checks without hardware:

```bash
python3 -m teslatron_services --config config/heliox_local_gui.example.json --port 8767
```

## 7. Live safety rules

For real-hardware sessions, the short version is:

- start with `read_only=true`
- verify readback before sending any command
- do not keep LabVIEW and Python connected to the same Mercury session at the same time
- test small ramps before large ones
- use `Hold` if behavior looks wrong

The full live workflow is documented in `LAB_RUNBOOK.md`.

## 8. Configuration files

Most service files use a top-level `cryostat` or `electrical` object.

### Cryostat config structure

Important cryostat config fields:

- `backend`: `mock`, `standard`, or `heliox`
- `read_only`: blocks command endpoints when true
- `poll_interval_s`: how often the service refreshes state
- `log_interval_s`: how often environment rows are written to disk
- `log_dir`: base folder for exported cryostat logs
- `recipe_dir`: storage for saved recipes
- `active_insert`: currently selected insert profile
- `insert_profiles`: insert-specific mapping and capabilities
- `sample_sensor_presets`: reusable Mercury sample-sensor setups
- `itc`: Mercury iTC VISA address and channel mapping
- `ips`: Mercury iPS VISA address and channel mapping
- `safety`: software-side limits for ramps and targets

The user-facing meaning is:

- `mock`: offline development or GUI checks
- `standard`: standard configuration
- `heliox`: Heliox-only configuration

The config loader also supports:

- insert-specific overrides for the iTC mapping
- switching the active insert at runtime through the API
- applying sample sensor presets at runtime

### Common cryostat config files in this repo

- `config/cryostat_mock.json`: safest offline development config
- `config/cryostat_lab_readonly.json`: real lab standard mapping with commands disabled
- `config/cryostat_lab_control.json`: real lab standard mapping with commands enabled
- `config/cryostat_standard.example.json`: standard example config
- `config/cryostat_standard_local_gui.json`: offline GUI config with standard insert profiles on the mock backend
- `config/heliox_readonly.example.json`: Heliox example, read-only
- `config/heliox_control.example.json`: Heliox example, writable
- `config/heliox_local_gui.example.json`: loopback-only frontend test config
- `config/cryostat_ethernet.example.json`: Ethernet-oriented standard example
- `config/cryostat_tcpip_candidates.json`: address candidates and notes

### Electrical config structure

Important electrical config areas:

- `cryostat`: URLs and polling settings for the cryostat service
- `measurement_session`: output directory
- `instruments`: electrical instrument definitions
- `vdp`: van der Pauw measurement resources
- `plans`: named measurement plans and triggers

The electrical service config is documented more deeply in:

- `ELECTRICAL_MEASUREMENT_ARCHITECTURE.md`
- `docs/electrical_measurements.md`

## 9. API overview

### Basic service endpoints

- `GET /`: web GUI
- `GET /health`: liveness check
- `GET /state`: latest cryostat snapshot
- `GET /config`: active configuration snapshot
- `WS /ws/state`: live state stream

### Configuration endpoints

- `POST /config/activate-insert`: switch insert profile
- `POST /config/apply-sample-sensor`: apply a sample sensor preset

### Diagnostics endpoints

- `GET /diagnostics`
- `GET /diagnostics/resources`
- `GET /diagnostics/catalog`
- `GET /diagnostics/readings`
- `POST /diagnostics/query`

These are useful for:

- confirming current VISA resources
- checking whether a specific raw reading succeeds
- comparing service behavior against manual Mercury queries

### Command endpoints

Common write endpoints include:

- `POST /commands/ramp-temperature`
- `POST /commands/temperature/{loop}/ramp`
- `POST /commands/temperature/{loop}/target`
- `POST /commands/temperature/{loop}/fixed-heater`
- `POST /commands/temperature/{loop}/pid`
- `POST /commands/ramp-field`
- `POST /commands/ramp-to-zero`
- `POST /commands/clamp`
- `POST /commands/hold`
- `POST /commands/abort`
- `POST /commands/vti/gas/set-needle`
- `POST /commands/vti/gas/set-pressure`
- `POST /commands/ips/switch-heater`

When `read_only=true`, these endpoints reject write attempts instead of sending
Mercury `SET` commands.

### Recipe endpoints

- `GET /recipes`
- `GET /recipes/{recipe_id}`
- `POST /recipes/start`
- `POST /recipes/save`
- `POST /recipes/acknowledge`
- `POST /recipes/signal`
- `POST /recipes/abort`
- `DELETE /recipes/{recipe_id}`
- `POST /recipes/{recipe_id}/rename`
- `POST /recipes/{recipe_id}/duplicate`

Saved recipes are kept under the configured `recipe_dir`.

### External measurement endpoints

- `GET /measurement-context`
- `GET /external-measurements/pending`
- `POST /external-measurements/complete`

These endpoints are intended for LabVIEW or other external acquisition tools.

## 10. Common workflows

### Read-only hardware inspection

Use this when you only want to confirm connections and live values:

1. start `config/cryostat_lab_readonly.json`
2. open the GUI
3. inspect `GET /state` and `GET /diagnostics/readings`
4. verify no other VISA client is fighting for the same controllers

### Controlled live session

Use this when you intend to send commands:

1. confirm the read-only path works first
2. switch to `config/cryostat_lab_control.json`
3. test `Hold`
4. test a small temperature ramp
5. test a small field ramp

### LabVIEW-assisted acquisition

Use this when external software must correlate electrical data with cryostat
state:

1. keep the cryostat service as the only Mercury owner
2. let LabVIEW poll `GET /measurement-context`
3. use the external-measurement handshake endpoints for recipe coordination
4. merge high-rate electrical data with cryostat timestamps offline if needed

### Offline GUI validation

Use this when you are changing the frontend and want interactive controls
without hardware:

1. run `config/cryostat_mock.json` or `config/heliox_local_gui.example.json`
2. verify forms, state refresh, and diagnostics rendering
3. keep live hardware completely disconnected from that session

## 11. Output files

### Cryostat environment logs

The cryostat service periodically saves environment data under the configured
`log_dir`. A helper script is included to inspect these CSV exports:

```bash
python3 tools/inspect_environment_log.py data/cryostat_environment_YYYY-MM-DD.csv
```

### Electrical measurement outputs

Electrical runs produce output under:

```text
data/electrical/YYYY-MM-DD/<run_id>/
```

Typical outputs include:

- a JSONL event log
- a CSV table for analysis
- configuration and cryostat snapshots for traceability

The CSV includes:

- run metadata
- absolute and relative timestamps
- cryostat context such as temperature and field
- flattened electrical measurement fields

See `docs/electrical_measurements.md` for an example row and field details.

## 12. Troubleshooting

### The GUI opens but values do not update

Check:

- whether the backend really started
- whether the selected config points to valid VISA resources
- whether another client is still holding the same controller session

### Commands return 403

Usually this means the loaded config is read-only. Confirm `GET /config` and
check the `read_only` flag.

### The service can read hardware sometimes but not reliably

Check for VISA session conflicts, especially if LabVIEW is still connected to
the same iTC or iPS.

### Heliox controls behave differently from Mercury controls

That is expected. The Heliox backend intentionally exposes a more abstract
sample-control model and does not mirror every low-level Mercury tuning path.

## 13. Which document should I read next

- Read `LAB_RUNBOOK.md` if you are about to use live hardware.
- Read `SERVICE_ARCHITECTURE.md` if you need endpoint-by-endpoint or backend-level detail.
- Read `ELECTRICAL_MEASUREMENT_ARCHITECTURE.md` if you are working on external or automated measurements.
- Read `docs/manuals/` when you need vendor-level or raw mapping references.
