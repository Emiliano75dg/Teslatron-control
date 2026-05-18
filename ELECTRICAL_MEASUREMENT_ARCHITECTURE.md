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

In practice, the service should support two high-level operating modes from the
start:

- `continuous`: acquire while cryostat variables are evolving
- `command-driven`: acquire only when an explicit command or recipe event says
  so

These two modes should share the same driver layer and persistence model, but
use different scheduling policies.

## Operating modes

### 1. Continuous acquisition

This mode is useful when electrical data must be collected while temperature or
magnetic field are changing continuously.

Typical examples:

- resistance versus temperature during a ramp
- current leakage while field is sweeping
- continuous voltage/current monitoring during pressure stabilization

Recommended behavior:

- run on its own cadence, independent from cryostat log cadence
- optionally downsample or average at save time
- attach the latest cryostat snapshot to every measurement event
- allow `safe_to_measure` to pause acquisition if the cryostat enters an unsafe
  state

Recommended config fields:

- acquisition interval
- instrument action
- optional route definition
- save-every-point versus save-batched policy
- optional pause/resume rules tied to cryostat safety

### 2. Command-driven or recipe-driven acquisition

This mode is useful when the electrical measurement is a discrete experiment
step rather than a background stream.

Typical examples:

- reach stable temperature, then perform one IV sweep
- change field, then measure resistance once
- wait for operator confirmation, then re-route the matrix and repeat

Recommended behavior:

- the electrical service exposes an explicit command API
- the cryostat recipe can trigger named electrical actions
- electrical plans can acknowledge completion back to the cryostat layer

This is the mode that best supports workflows such as:

1. ramp temperature
2. wait for stability
3. perform IV measurement
4. change temperature
5. repeat the same IV measurement

That sequence should not be encoded as ad hoc code in the cryostat backend.
Instead, the cryostat recipe should emit synchronization points and the
electrical service should execute the requested measurement plan when those
points are reached.

## Completion handshake with cryostat recipes

For recipe-driven measurements, the trigger alone is not enough. The cryostat
recipe must also know when the electrical measurement has actually finished so
that it can continue safely to the next step.

This means the integration must be a two-phase handshake:

1. the cryostat recipe emits a measurement request
2. the electrical service starts the requested plan
3. the electrical service runs the real measurement workflow
4. the electrical service reports a terminal result
5. only then does the cryostat recipe continue

The terminal result should distinguish at least:

- `completed`
- `failed`
- `aborted`
- `timed_out`

This is important because "measurement started" and "measurement completed" are
not equivalent, especially for:

- IV sweeps that take several seconds or minutes
- switch-matrix sequences with settling delays
- multi-instrument measurements where one device may fail after the trigger

## Recommended recipe integration pattern

The cleanest pattern is:

1. cryostat recipe reaches the desired physical condition
2. cryostat recipe emits a named signal such as `measure_iv`
3. cryostat recipe enters a wait state for measurement completion
4. electrical service receives the signal and starts the matching plan
5. electrical service emits a completion signal such as
   `measurement_completed:measure_iv` or `measurement_failed:measure_iv`
6. cryostat recipe resumes or aborts based on that result

In other words, recipe-driven electrical measurements should behave like
blocking recipe steps from the point of view of the cryostat workflow, even if
the measurement execution itself is asynchronous internally.

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

### Recommended trigger vocabulary

To cover the use cases above cleanly, the first plan schema should include:

- `interval`
  - continuous periodic acquisition during ramps or holds
- `manual`
  - an operator or API explicitly starts the measurement
- `recipe_signal`
  - the cryostat recipe emits a named signal such as `measure_iv`
- `cryostat_stable`
  - measurement starts only after temperature or field stability conditions are
    satisfied

The important point is that `cryostat_stable` and `recipe_signal` are not the
same thing:

- `cryostat_stable` is a physical condition
- `recipe_signal` is a workflow instruction

In many experiments you will want both:

1. the cryostat recipe ramps to a target
2. the recipe waits until the system is stable
3. the recipe emits `measure_iv`
4. the electrical service receives that signal and runs the IV plan
5. the electrical service emits a terminal completion signal
6. the cryostat recipe continues only after receiving that completion

## Recommended signal vocabulary

To avoid ambiguity, use separate signal names for:

- request-to-start
- successful completion
- unsuccessful completion

For example:

- request: `measure_iv`
- success: `measure_iv.completed`
- failure: `measure_iv.failed`
- abort: `measure_iv.aborted`

An alternative is a generic completion endpoint carrying structured payload:

```json
{
  "signal": "measure_iv",
  "status": "completed",
  "run_id": "iv_at_4k",
  "plan_id": "iv_4probe",
  "message": "Sweep finished successfully"
}
```

This second form is preferable because the cryostat service can log and inspect
the measurement outcome rather than only receiving a plain string.

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
- operating mode
- trigger condition
- measurement steps
- settling and timeout rules
- save policy
- metadata tags such as sample, operator, contact map, insert
- completion signaling policy for recipe synchronization

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
      "id": "rt_monitor",
      "mode": "continuous",
      "trigger": {
        "type": "interval",
        "interval_s": 1.0
      },
      "steps": [
        {
          "instrument": "pico",
          "action": "read_current"
        }
      ]
    },
    {
      "id": "iv_4probe",
      "mode": "command-driven",
      "trigger": {
        "type": "recipe_signal",
        "signal": "measure_iv"
      },
      "completion": {
        "notify_recipe": true,
        "success_signal": "measure_iv.completed",
        "failure_signal": "measure_iv.failed"
      },
      "route": {
        "matrix": ["A1-B1", "A2-B2"]
      },
      "preconditions": {
        "require_safe_to_measure": true,
        "sample_temperature_stable": true
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
- `mode`
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
4. two modes: continuous periodic measurement and command-driven single plan execution
5. two trigger types: `interval` and `recipe_signal`
6. one shared resource lock for the switch matrix
7. save measurement events to JSONL

After that, add:

- cryostat-stability triggers
- route lists for multiplexed measurements
- SMU sweep actions
- recipe completion acknowledgements back to the cryostat layer
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
- `POST /plans/trigger`
- `POST /plans/recipe-signal`
- `POST /plans/validate`
- `GET /results/latest`
- `WS /ws/state`

On the cryostat side, recipe integration should also support:

- waiting for an external measurement completion signal
- recording the terminal status of the electrical plan
- aborting or retrying based on measurement failure

## Cryostat-side LabVIEW handshake

The cryostat service now exposes a simple HTTP handshake for LabVIEW or other
external software that needs cryostat context while electrical measurements are
running.

Core polling endpoint:

- `GET /measurement-context`

Recommended payload:

```json
{
  "timestamp_unix_s": 1710000000.123,
  "timestamp_iso": "2024-03-09T10:00:00.123Z",
  "sample_temperature_K": 4.21,
  "field_T": 1.5,
  "safe_to_measure": true
}
```

Use explicit fields, not anonymous arrays such as `[T, B]`.

For recipe synchronization, the cryostat service also provides:

- `GET /external-measurements/pending`
- `POST /external-measurements/complete`
- `POST /recipes/signal`

Recipe support is based on an `external_measurement` step with:

- `mode: "point"` for stable-point measurements that pause the recipe
- `mode: "start"` to request the start of a continuous acquisition before a ramp
- `mode: "stop"` to request the stop of the continuous acquisition after a ramp

Typical continuous-flow sequence:

1. the recipe sends `R_vs_T.start`
2. LabVIEW confirms `R_vs_T.started`
3. the cryostat recipe proceeds into the ramp
4. LabVIEW polls `GET /measurement-context` during the ramp
5. the recipe later sends `R_vs_T.stop`
6. LabVIEW confirms `R_vs_T.stopped`

For slow acquisitions around 1-5 Hz, HTTP polling is a simple adequate
solution. For faster data collection, timestamps should be recorded on both
sides and merged offline.

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
