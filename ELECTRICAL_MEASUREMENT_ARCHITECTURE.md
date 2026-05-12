# Electrical Measurement Architecture

This note proposes how to extend the Teslatron control stack with asynchronous
electrical measurements while keeping cryostat control and electrical hardware
ownership clearly separated.

## Goals

- keep the cryostat service as the single source of truth for environment state
- support asynchronous measurements from heterogeneous instruments
- allow per-experiment configuration without code changes
- correlate every electrical measurement with the cryostat state
- prevent conflicts on shared resources such as switch matrices

## Recommended split

Use two service layers:

1. `cryostat service`
   - owns Mercury iTC / iPS or Heliox-backed cryostat control
   - polls environment every second
   - logs environment periodically
   - exposes live state, safety flags, recipes, and synchronization signals

2. `electrical measurement service`
   - owns only electrical instruments
   - subscribes to cryostat state from `/state` or `WS /ws/state`
   - runs measurement plans independently from the cryostat polling cadence
   - saves electrical measurements together with a cryostat snapshot

This matches the existing architectural direction in `SERVICE_ARCHITECTURE.md`:
electrical measurement services should read cryostat state instead of opening
Mercury controllers directly.

## Why not put electrical logic inside the cryostat service

Embedding electrical measurements into the cryostat service would make one
process responsible for:

- long-lived environment polling
- safety-critical cryostat commands
- instrument-specific measurement workflows
- routing and settling logic for switch matrices
- potentially slow blocking reads from multiple VISA devices

That coupling would make the service harder to test, harder to recover after an
instrument failure, and more fragile during live operation.

## Core model

The cryostat stream and the measurement stream should be treated as different
data products.

### 1. Environment stream

Regular state snapshots produced by the cryostat service:

- timestamp
- temperatures
- field
- pressure
- switch heater state
- safety flags such as `safe_to_measure`
- recipe status if relevant

### 2. Measurement event stream

Asynchronous records produced by the electrical service:

- timestamp
- run/session identifier
- plan identifier
- route identifier
- instrument result payload
- instrument configuration used
- latest cryostat snapshot at acquisition time

This keeps the present 1 s / 20 s cryostat cadence intact while allowing
electrical measurements to happen:

- on demand
- periodically with a different cadence
- in bursts during sweeps
- only after stability conditions are met

## Measurement service responsibilities

The electrical service should contain four distinct layers.

### Instrument drivers

Each instrument gets a small driver with a common lifecycle:

- `connect()`
- `configure(config)`
- `arm()`
- `measure()` or `trigger()`
- `fetch()`
- `shutdown()`

Examples:

- switch matrix
- SMU
- picoammeter
- voltmeter
- lock-in

Each driver should own only the commands for that instrument, not orchestration.

### Resource manager

A resource manager coordinates shared access to physical resources:

- VISA sessions
- switch matrix routes
- shared trigger lines
- source/measure channels

For example, the switch matrix should be treated as a locked shared resource so
that no two measurement tasks re-route the sample path concurrently.

### Measurement orchestrator

The orchestrator executes configurable measurement plans:

- loads plan definitions
- waits for triggers or cryostat conditions
- acquires required resource locks
- configures routes and instruments
- enforces settling delays
- triggers acquisitions
- fetches results
- attaches cryostat context
- persists output

### Persistence layer

The persistence layer writes structured outputs for later analysis:

- one file or directory tree per run
- separate environment and electrical records
- shared identifiers for later join/merge

## Recommended synchronization model

The cleanest contract between services is event-driven, not tightly coupled.

Use these cryostat-side inputs:

- `GET /state` for simple polling
- `WS /ws/state` for continuous live updates
- recipe signals for coarse synchronization
- `safety.safe_to_measure` as a basic interlock

The electrical service should keep an in-memory copy of the latest cryostat
state and stamp each measurement with:

- acquisition timestamp
- cryostat timestamp
- relevant stability flags
- current recipe step or signal if available

## Triggers

Measurement plans should support multiple trigger types.

### Time-based

- every `N` seconds
- every `N` points of another sweep

### Cryostat-state-based

- when sample temperature becomes stable
- when VTI pressure enters a target window
- when field reaches setpoint
- when `safe_to_measure` becomes true

### Recipe-based

- when the cryostat recipe publishes a named signal
- when an operator acknowledges a pause state

### Manual or API-driven

- operator presses measure
- client calls `POST /measurements/start`

## Switch matrix handling

The switch matrix is the main place where asynchronous work can become unsafe or
inconsistent. Treat a route change as an atomic sequence:

1. acquire matrix lock
2. open or reset previous path if needed
3. close the requested route set
4. wait for relay settling
5. configure active instruments
6. acquire data
7. release or move to the next route

If multiple plans are active, the scheduler should serialize any work that
touches the matrix even if other instruments could, in principle, run in
parallel.

## Plan-based configuration

Avoid one-off scripts for each experiment. Instead, define plans in JSON or
YAML.

Each plan should describe:

- instruments required
- optional routes
- trigger condition
- measurement steps
- settling and timeout rules
- save policy
- metadata tags such as sample, operator, contact map, insert

### Example structure

```json
{
  "measurement_session": {
    "run_id": "2026-05-12_sampleA",
    "save_dir": "data/electrical"
  },
  "instruments": {
    "matrix": {
      "driver": "keithley_7001",
      "visa": "GPIB0::9::INSTR"
    },
    "smu": {
      "driver": "keithley_2450",
      "visa": "TCPIP0::172.31.109.200::INSTR"
    },
    "pico": {
      "driver": "keithley_6485",
      "visa": "GPIB0::22::INSTR"
    }
  },
  "plans": [
    {
      "id": "iv_4probe",
      "trigger": {
        "type": "cryostat_stable",
        "temperature_tolerance_K": 0.01,
        "stable_s": 60
      },
      "route": {
        "matrix": ["A1-B1", "A2-B2"]
      },
      "steps": [
        {
          "instrument": "smu",
          "action": "sweep_current",
          "start_A": -0.001,
          "stop_A": 0.001,
          "points": 101
        },
        {
          "instrument": "pico",
          "action": "read_current"
        }
      ]
    }
  ]
}
```

## Suggested Python module layout

One possible structure inside this repository:

```text
teslatron_services/
  cryostat/
  electrical/
    api.py
    cli.py
    config.py
    orchestrator.py
    persistence.py
    state.py
    resources.py
    plans.py
    drivers/
      base.py
      keithley_2450.py
      keithley_6485.py
      keithley_7001.py
```

Suggested responsibilities:

- `config.py`: load electrical service config and plan definitions
- `state.py`: measurement session state and run metadata
- `resources.py`: shared locks and instrument/session registry
- `plans.py`: validation and normalization of plan definitions
- `orchestrator.py`: async execution engine
- `persistence.py`: JSONL/CSV writing and run directory management
- `api.py`: endpoints for status, start, stop, plan control, and results

## Data persistence recommendation

Do not merge cryostat environment rows and electrical data rows into a single
CSV from the start.

Prefer:

- `cryostat_environment_YYYY-MM-DD.csv` from the existing service
- one measurement run directory under `data/electrical/`
- JSONL or CSV files for measurement events
- one metadata file per run

Recommended run metadata:

- `run_id`
- sample identifier
- insert/profile
- operator
- plan ids enabled
- instrument addresses
- software version or git commit if available

Recommended measurement record fields:

- `timestamp`
- `run_id`
- `plan_id`
- `event_id`
- `route_id`
- `instrument`
- `action`
- result values
- cryostat snapshot summary such as `sample_temperature_K`, `field_T`,
  `pressure_mbar`, `safe_to_measure`

## Safety rules

At minimum, the electrical orchestrator should support:

- block or pause plans when `safe_to_measure` is false
- time out if the cryostat state becomes stale
- abort cleanly if an instrument disconnects
- leave source outputs in a safe state on failure
- optionally return switch matrix to a known route set

Later, this can grow into finer-grained safety policies such as:

- forbidden source current above a field threshold
- no route switching while current output is enabled
- different limits by insert/profile

## Recommended MVP

The smallest useful first implementation would be:

1. electrical service with its own FastAPI app
2. one driver interface plus one mock driver
3. subscription to cryostat `GET /state` or `WS /ws/state`
4. one plan type: periodic measurement
5. one conditional trigger: wait until `safe_to_measure == true`
6. one shared resource lock for the switch matrix
7. save measurement events to JSONL

After that, add:

- cryostat-stability triggers
- route lists for multiplexed measurements
- SMU sweep actions
- recipe-signal synchronization
- GUI support

## Recommended API sketch

The electrical service could expose:

- `GET /health`
- `GET /config`
- `GET /state`
- `GET /runs`
- `POST /runs/start`
- `POST /runs/stop`
- `POST /plans/start`
- `POST /plans/stop`
- `POST /plans/validate`
- `GET /results/latest`
- `WS /ws/state`

## Practical recommendation

For this project, the best long-term direction is:

- keep cryostat control simple and authoritative
- move electrical work into a dedicated orchestration service
- make instruments pluggable through drivers
- make experiments configurable through plans
- save asynchronous electrical events separately, but always with cryostat
  context attached

That gives flexibility for switching between SMU, picoammeter, matrix, and
future instruments without repeatedly redesigning the core service.
