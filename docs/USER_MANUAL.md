# User Manual — Teslatron Control

This manual is for anyone using the Teslatron control software for the first time.
It explains how to start the program, how to navigate the GUI, and how to use every
feature, including the recipe system and the template buttons.

---

## Table of contents

1. [What this software does](#1-what-this-software-does)
2. [Quick start](#2-quick-start)
3. [The graphical interface](#3-the-graphical-interface)
4. [Overview tab](#4-overview-tab)
5. [ITC tab](#5-itc-tab)
6. [IPS tab](#6-ips-tab)
7. [Plots tab](#7-plots-tab)
8. [Commands tab](#8-commands-tab)
9. [Recipes tab — complete guide](#9-recipes-tab--complete-guide)
10. [External Measurements tab](#10-external-measurements-tab)
11. [Config tab](#11-config-tab)
12. [Log Viewer (offline tool)](#12-log-viewer-offline-tool)
13. [Safety rules](#13-safety-rules)
14. [Troubleshooting](#14-troubleshooting)
15. [Glossary](#15-glossary)

---

## 1. What this software does

Teslatron Control is a web application that runs locally on the lab PC and lets
you operate the Teslatron cryostat of the Q-MAT laboratory (CNR-SPIN, University
of Naples Federico II).

The GUI opens in a **browser** (Chrome, Firefox, Edge). It is not a traditional
Windows desktop application — it is an HTTP server that listens on
`http://127.0.0.1:876X/`.

What it controls:

| Quantity | Instrument |
|----------|------------|
| Sample and VTI temperature | Mercury iTC |
| Magnetic field | Mercury iPS |
| VTI gas pressure / needle valve | Mercury iTC (gas board) |
| External electrical measurements | LabVIEW (coordinated via HTTP) |

---

## 2. Quick start

### Prerequisites (first time only)

```powershell
pip install -r requirements-service.txt
pip install pyvisa
python -m pip install --user -e .
```

### Starting the service

Open a PowerShell terminal in the project folder and use one of these commands:

| Command | What it does | Port |
|---------|-------------|------|
| `teslatron` | Safe mock mode — no hardware | 8765 |
| `teslatron readonly` | Read live lab data, no commands | 8765 |
| `teslatron control` | Full lab control | 8766 |
| `teslatron heliox` | Heliox probe session | 8767 |
| `teslatron --open-browser` | Start and open the browser automatically | 8765 |

After startup, open the browser at the address printed in the terminal, typically
`http://127.0.0.1:8765/`.

> **First time?** Always start with `teslatron` (mock) to explore the interface
> safely. No hardware is touched.

### Stopping the service

- **In the terminal** where the service was started: press `Ctrl+C`.
- **From the GUI**: Commands tab → **Shutdown service** button.
- **From any terminal**: `teslatron-stop`.

Closing the browser tab **does not** stop the service.

---

## 3. The graphical interface

### Status bar

The top bar always shows the global system state:

```
[Service: Connected] [Backend: mock] [Mode: IDLE] [Safety: ok] [Access: Writable]
```

| Indicator | Meaning |
|-----------|---------|
| **Service** | `Connected` = backend OK; `Error` = communication problem |
| **Backend** | `mock` (simulation), `standard` (real Mercury), `heliox` (Heliox probe) |
| **Mode** | `IDLE`, `RAMP_T`, `RAMP_B`, `HOLD`, `RECIPE`, `ERROR` |
| **Safety** | `ok`, `warning`, `critical` — software-side limits |
| **Access** | `READ ONLY` = commands blocked; `Writable` = commands enabled |

If **Access** shows `READ ONLY`, all command buttons are disabled. Restart with
`teslatron control` to enable commands.

### Tab navigation

The main tabs are displayed below the status bar:

```
Overview | ITC | IPS | Plots | Commands | Recipes | External | Config
```

Click a tab to switch to that section. The active tab is highlighted.

---

## 4. Overview tab

Real-time monitoring panel. Updates automatically every few seconds via WebSocket —
no page reload needed.

Displays:

- **Temperature**: Sample, VTI, Magnet, PT1, PT2
- **Field**: magnetic field in Tesla, IPS current and voltage
- **Pressure**: VTI gas pressure in mbar
- **Trend charts**: 30-minute history for the main quantities

This is the right view to keep open during an acquisition.

---

## 5. ITC tab

Details from the Mercury iTC (temperature controller).

Shows the two **temperature loops** side by side:

### Sample loop

- Current temperature read from the sensor
- Active temperature target
- Heater power (%)
- PID mode (auto / manual)
- Status: `RAMP`, `STABLE`, `HOLD`

### VTI loop (Variable Temperature Insert)

- Current VTI temperature
- Same controls as the sample loop
- Gas needle and pressure readback

> Values in this tab are read-only. To send commands, go to the **Commands** tab.

---

## 6. IPS tab

Details from the Mercury iPS (superconducting magnet power supply).

Displays:

- **Field**: current magnetic field in Tesla
- **Current**: magnet current in Ampere
- **Voltage**: supply voltage
- **Switch heater**: persistent-switch heater state (On / Off)
- **Magnet temperature**: magnet cryostat temperature
- **PT temperatures**: PT1, PT2 (cryogenic reference points)

> The **switch heater** is critical: it must be **On** before field ramps and
> **Off** for persistent-mode operation. Do not toggle it manually unless you
> know the correct procedure (see `LAB_RUNBOOK.md`).

---

## 7. Plots tab

Customisable interactive charts.

You can select:

- **Which quantities to display** (sample temperature, VTI, field, pressure, …)
- **Time window**: from 5 minutes up to 24 hours
- **Zoom and pan**: use the scroll wheel or drag along the X axis

Data is read from the CSV log files saved under `data/`. If the log was just
started, only a short history will be visible.

---

## 8. Commands tab

This is where commands are sent to the cryostat. Available only when
**Access: Writable**.

### Ramp Temperature

Sets a controlled temperature ramp:

| Field | Description |
|-------|-------------|
| Loop | `sample`, `vti`, or `both` (VTI targets 90 % of the sample target) |
| Target K | Final temperature in Kelvin |
| Rate K/min | Ramp speed (e.g. `1.0` K/min) |
| Tolerance K | Stability band (e.g. `0.05` K) |
| Stable s | Seconds to remain inside the band before declaring "stable" |

Click **Ramp** to start. The **Mode** indicator will change to `RAMP_T`.

### Set Temperature Target

Sets a temperature set point without specifying a rate (uses the Mercury iTC
internal ramp).

### Ramp Field

Sets a controlled field ramp:

| Field | Description |
|-------|-------------|
| Target T | Final field in Tesla |
| Rate T/min | Ramp speed |
| Tolerance T | Stability band |

### Field to Zero

Ramps the field to zero safely at the configured rate.

### Hold

Puts the system into Hold mode: stops any active ramp and holds the current
value. Use this if something looks wrong.

### Abort

Immediately stops any ongoing operation (ramp, recipe, etc.). More drastic than
Hold.

### VTI Gas Controls

- **Set Needle**: manually sets the needle valve position (0–100 %)
- **Set Pressure**: sets the VTI gas pressure target in mbar

### Switch Heater

Turns the iPS persistent-switch heater on or off. Follow the correct procedure
before using this (see `LAB_RUNBOOK.md`).

### Shutdown Service

Stops the Python backend cleanly.

---

## 9. Recipes tab — complete guide

The **Recipes** system lets you program automatic multi-step sequences that run
unattended.

A recipe is an ordered list of **steps**. Each step is one operation (temperature
ramp, wait, measurement, etc.). The backend executes them in order, moving to the
next step only when the current one finishes.

---

### Screen layout

```
┌─────────────────────────────────────────────────────────────────────┐
│  RECIPE BUILDER                                        [Idle badge]  │
├─────────────────────────────────────────────────────────────────────┤
│  Banner: use external_measurement steps for LabVIEW coordination     │
├─────────────────────────────────────────────────────────────────────┤
│  Recipe name: [Cryostat recipe            ]                          │
│                                                                      │
│  [Add point template]   [Add continuous-ramp template]  ← TEMPLATES │
├─────────────────────────────────────────────────────────────────────┤
│  Saved recipes: [dropdown]  [Load] [Save] [Rename] [Dup] [Delete]   │
├─────────────────────────────────────────────────────────────────────┤
│  Step: [Ramp T ▼]  Loop:[sample▼]  Target K:[4.2]  Rate:[1]  …      │
│                                                          [Add]       │
├─────────────────────────────────────────────────────────────────────┤
│  STEPS (numbered list)                                               │
│  1. ramp_temperature: sample → 300 K @ 1.0 K/min        [×]         │
│  2. external_measurement: point — measure_iv             [×]         │
│  …                                                                   │
├─────────────────────────────────────────────────────────────────────┤
│  Run controls                                                        │
│  [Start Recipe]  [Clear]  [Continue]  [Abort Recipe]                │
└─────────────────────────────────────────────────────────────────────┘
```

To the right of the builder there is a **status card** showing the currently
running recipe and a LabVIEW endpoint reference.

---

### Available step types

| Type (Step dropdown) | What it does |
|----------------------|--------------|
| **Ramp T** | Ramps temperature to the target at the specified rate |
| **Set T target** | Sets a temperature set point (no explicit rate) |
| **Ramp B** | Ramps magnetic field to the target at the specified rate |
| **B to zero** | Ramps field to zero |
| **Wait** | Waits a fixed number of seconds |
| **Wait signal** | Waits for an external signal (e.g. from LabVIEW) |
| **External measurement** | Coordinates a measurement with LabVIEW or other external software |

---

### Adding steps manually

1. Select the step type from the **Step** dropdown.
2. Fill in the fields that appear (they change depending on the type).
3. Click **Add** — the step appears in the numbered list.
4. Repeat for each step in the sequence.
5. To remove a step, click the **×** next to it.

---

### The template buttons — detailed explanation

These two buttons are **shortcuts**: they insert one or more pre-configured steps
with the most common default values, ready to use with LabVIEW.

---

#### Button: `Add point template`

**Adds 1 step** of type `external_measurement` in **point** mode.

What happens when the recipe reaches this step:

1. The recipe **pauses** and waits.
2. The software signals to LabVIEW (or any other external program) that it is
   time to run a measurement (e.g. an IV curve).
3. LabVIEW runs the measurement.
4. LabVIEW notifies completion via HTTP (`POST /external-measurements/complete`).
5. The recipe **resumes** with the next step.

Pre-set values of the generated step:

| Field | Value |
|-------|-------|
| Mode | `point` |
| Request signal | `measure_iv` |
| Completion signal | `measure_iv.completed` |
| Failure signal | `measure_iv.failed` |
| Timeout | 600 seconds (10 min) |
| Message | "Run IV measurement in LabVIEW" |

**When to use it**: when you want to stop at a stable temperature or field point
and take a single measurement before continuing the sequence.

**Typical recipe using the point template**:

```
1. Ramp T: sample → 10 K @ 2 K/min
2. Wait: 60 s  (stabilisation)
3. [point template] → LabVIEW measures IV at 10 K
4. Ramp T: sample → 20 K @ 2 K/min
5. Wait: 60 s
6. [point template] → LabVIEW measures IV at 20 K
…
```

---

#### Button: `Add continuous-ramp template`

**Adds 3 steps at once**:

```
Step A:  external_measurement (mode: start)
Step B:  ramp_temperature (sample → 300 K @ 1.0 K/min)
Step C:  external_measurement (mode: stop)
```

What happens when the recipe reaches these steps:

1. **Step A** — The software signals LabVIEW to **start continuous acquisition**.
   LabVIEW confirms that acquisition has started, then the recipe proceeds.
2. **Step B** — The temperature ramp begins. LabVIEW acquires data continuously
   while the temperature rises (typical for R vs T measurements).
3. **Step C** — The ramp is finished. The software signals LabVIEW to **stop
   acquisition**. LabVIEW confirms, then the recipe continues.

Pre-set values:

| Step | Field | Value |
|------|-------|-------|
| A (start) | Request signal | `R_vs_T.start` |
| B (ramp) | Target K | 300 K |
| B (ramp) | Rate K/min | 1.0 |
| B (ramp) | Loop | sample |
| C (stop) | Request signal | `R_vs_T.stop` |

**When to use it**: for resistance-versus-temperature (R vs T) measurements or
any quantity that LabVIEW must acquire continuously while the system ramps.

After clicking the button you can adjust the ramp target and rate: remove step B
with **×** and add a new Ramp T step with the desired values, then re-run from
the beginning.

---

### Full workflow: build and run a recipe

#### 1. Build the recipe

Use the manual step form and/or the template buttons. Steps appear in the
numbered list as you add them.

#### 2. Name the recipe

Fill in the **Recipe name** field (e.g. `R_vs_T_sample_A`).

#### 3. Save the recipe (optional but recommended)

Click **Save**. The recipe is stored under the configured `recipe_dir` and
appears in the **Saved recipes** dropdown. You can reload it later with **Load**.

Other management buttons:

| Button | Action |
|--------|--------|
| **Load** | Load the recipe selected in the dropdown into the builder |
| **Save** | Save the current recipe (will not overwrite an existing one unless you confirm) |
| **Rename** | Rename the selected saved recipe |
| **Duplicate** | Create a copy with a new name |
| **Delete** | Permanently delete the selected saved recipe |

#### 4. Start the recipe

Click **Start Recipe**. The badge changes from `Idle` to `Running`.

The recipe starts immediately from the top and works down step by step. The
current state is shown in the status card on the right:

- **Name**: name of the running recipe
- **Status**: `running`, `waiting`, `paused`, `completed`, `error`
- **Step**: which step is active (e.g. `2 / 5`)
- **Message**: descriptive message for the current step

#### 5. Managing a running recipe

| Button | When to use |
|--------|-------------|
| **Continue** | When the recipe is paused on a `Wait signal` or `external_measurement` step and you want to advance it manually (without waiting for LabVIEW) |
| **Abort Recipe** | To stop the recipe immediately. The system returns to IDLE but **does not** cancel any ramp that may be in progress — use Hold in the Commands tab if needed |

#### 6. Recipe completion

When all steps finish, the status becomes `completed` and the badge returns to
`Idle`. Logs are saved automatically throughout.

---

### Worked example: R vs T from 4 K to 300 K with LabVIEW

**Goal**: measure sample resistance versus temperature while the system ramps
from 4 K to 300 K.

**Building the recipe**:

1. Click **Add continuous-ramp template** — 3 steps are added.
2. The template targets 300 K at 1.0 K/min. Make sure the sample is already at
   4 K before starting the recipe.
3. Name the recipe, e.g. `R_vs_T_300K`.
4. Click **Save**.
5. Click **Start Recipe**.

**What happens**:

```
Step 1 (start):  Recipe signals LabVIEW "R_vs_T.start"
                 LabVIEW confirms acquisition is running
Step 2 (ramp):   Temperature rises from ~4 K to 300 K @ 1 K/min
                 LabVIEW acquires R continuously
Step 3 (stop):   Recipe signals LabVIEW "R_vs_T.stop"
                 LabVIEW stops acquisition and confirms
                 Recipe: completed
```

---

### Worked example: IV curves at discrete temperatures

**Goal**: measure IV curves at 10 K, 50 K, 100 K, 200 K, and 300 K.

**Building the recipe**:

1. Add step: **Ramp T**, sample, 10 K, 2 K/min.
2. Add step: **Wait**, 120 seconds (stabilisation).
3. Click **Add point template** — adds the IV measurement step.
4. Repeat steps 1–3 for 50 K, 100 K, 200 K, and 300 K.
5. Save the recipe.
6. Click **Start Recipe**.

**Result**: the recipe stops automatically at each temperature, waits for
LabVIEW to complete the IV measurement, then moves to the next temperature.

---

## 10. External Measurements tab

Monitoring panel for external measurements coordinated via HTTP.

Displays:

- **Pending**: whether a measurement request is currently active (`true` / `false`)
- **Mode**: `point`, `start`, or `stop`
- **Request / Completion / Failure signal**: configured signal names
- **Live measurement context**: real-time values LabVIEW can read (sample
  temperature, field, magnet, …)

### Endpoints LabVIEW should use

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/measurement-context` | GET | Read temperature, field, and status in real time |
| `/external-measurements/pending` | GET | Check whether a measurement is requested |
| `/external-measurements/complete` | POST | Report completion of the measurement |
| `/recipes/signal` | POST | Send a signal directly to the recipe |

LabVIEW can poll continuously at 1–5 Hz. When no measurement is active,
`/external-measurements/pending` returns `{"pending": false}`.

> Do not use anonymous arrays such as `[T, B]` in LabVIEW. Always use explicit
> field names: `sample_temperature_K`, `field_T`, `magnet_temperature_K`, etc.

---

## 11. Config tab

Inspection and advanced configuration panel.

Displays:

- **Config snapshot**: summary of the loaded configuration file (backend, port,
  VISA addresses, sensor preset, …)
- **Insert profiles**: available probe profiles (Fisher, Basic, Heliox). Click a
  profile to switch the active insert — this reloads the channel mapping without
  restarting the service.
- **Sample sensor presets**: predefined setups for the sample temperature sensor.
  Select a preset and click **Apply** to switch the active sensor.

---

## 12. Log Viewer (offline tool)

The Log Viewer is a **separate** Streamlit tool for analysing CSV log files
outside of a live control session.

### Starting it

From a terminal:

```bash
teslatron-log
```

Alternatively, use the **Open Log Viewer** button in the GUI top bar — it starts
Streamlit automatically if it is not already running.

Then open the browser at:

```
http://127.0.0.1:8501/
```

### Using it

1. Upload one or more CSV files from the `data/` folder.
2. View overlaid traces with Plotly (zoom, pan, channel selection).
3. Browse reconstructed events (recipes, ramps, signals).
4. Preview the raw dataframe in a separate tab.

### Stopping it

- `Ctrl+C` in the terminal where Streamlit is running.
- Or: `teslatron-stop --include-log-viewer`.

---

## 13. Safety rules

### Rule 1 — Always start read-only

Use `teslatron readonly` for the first connection to the lab. Verify that the
readings look sensible before enabling commands.

### Rule 2 — LabVIEW and Python not at the same time

Do not keep LabVIEW connected to the Mercury iTC/iPS while Python is running.
Both clients compete for the same VISA session and connections will reset.

### Rule 3 — Test small ramps first

Before sending a large ramp, test with a small delta (e.g. ±1 K, ±0.1 T).

### Rule 4 — Hold if something looks wrong

If values appear anomalous, click **Hold** in the Commands tab immediately. Then
investigate.

### Rule 5 — Do not close the terminal without stopping the service

Closing the browser tab alone does not stop the backend. Run `teslatron-stop` or
press `Ctrl+C` in the terminal before leaving the lab.

---

## 14. Troubleshooting

### Values do not update

- Did the backend start? Check the terminal output.
- Does the config point to valid VISA addresses?
- Is another client (LabVIEW) holding the Mercury session open?

### A command button does not respond

- Check the status bar: **Access** must be `Writable`.
- If it shows `READ ONLY`, restart with `teslatron control`.

### The recipe is stuck on an `external_measurement` step

- LabVIEW must reply with `POST /external-measurements/complete`.
- If LabVIEW is unavailable, click **Continue** to unblock the step manually and
  let the recipe proceed.

### 403 error from an endpoint

The loaded config has `read_only: true`. Confirm with `GET /config`, then restart
with the control config.

### The log viewer shows no recent data

CSV files are written to the `data/` folder. Make sure you load the file for
today's date (e.g. `cryostat_environment_2026-06-18.csv`).

### The service reports that the port is already in use

Run `teslatron-stop` to release the ports, then restart.

---

## 15. Glossary

| Term | Meaning |
|------|---------|
| **Backend** | The driver that talks to hardware: `mock`, `standard`, or `heliox` |
| **ITC** | Mercury iTC — temperature controller |
| **IPS** | Mercury iPS — superconducting magnet power supply |
| **VTI** | Variable Temperature Insert — auxiliary cooling circuit |
| **Sample loop** | Temperature control loop for the sample sensor |
| **Hold** | Standby mode: holds current temperature / field with no ramp |
| **Recipe** | Programmed sequence of operations executed automatically |
| **Step** | A single operation inside a recipe |
| **Template** | A set of pre-configured steps inserted with one click |
| **Point measurement** | Measurement at a stable point: recipe pauses and waits for LabVIEW |
| **Continuous-ramp** | Continuous acquisition while the system ramps |
| **Switch heater** | Heater for the magnet persistent switch |
| **VISA** | Instrument communication standard (National Instruments) |
| **Mock** | Offline simulation — no hardware connected |
| **Read only** | Commands blocked for safety |
| **Writable** | Commands enabled |
| **Pending** | External measurement request waiting for a LabVIEW response |
| **Recipe dir** | Folder where recipes are saved as JSON files |
| **Log dir** | Folder where CSV environment logs are written |
