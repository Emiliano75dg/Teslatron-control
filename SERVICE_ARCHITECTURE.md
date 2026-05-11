# Teslatron service architecture

This is the first service-oriented layer for the Teslatron controller used on
the Q-MAT lab instrument, within CNR-SPIN and the Department of Physics
"E. Pancini" of the University of Naples Federico II.

## Roles

The cryostat service owns the cryostat hardware:

- Mercury iTC
- Mercury iPS
- environmental state such as temperature, field, pressure, heaters

The iTC temperature model is split into two independent loops:

- `sample`: sample/probe temperature loop
- `vti`: VTI temperature loop

Each loop has its own temperature, setpoint, rate, heater output, PID metadata,
mode, stability and ramping state. This mirrors the LabVIEW split between
Sample Loop Controls and VTI Loop Controls.

Gas/pressure control belongs to the VTI side of the cryostat. It has two
control modes:

- fixed needle valve opening, using the Mercury `FSET` field
- pressure control, using the Mercury `PRST` pressure setpoint field

`PSET` is intentionally not used for pressure here, because it is reserved for
power-related commands in our current working convention.

The iPS model includes:

- magnetic field readback, setpoint and sweep rate
- output current and voltage
- magnet temperature
- PT1 temperature
- PT2 temperature
- switch heater state, target state, delay and readiness

The switch heater uses the normal checked Mercury command `SWHT`. The forced
command `SWHN` is not used by the service API. When the switch heater is turned
on, the persistent switch is open/resistive and the magnet can be ramped after
the configured delay. When it is turned off, the persistent switch is
closed/superconducting after the configured delay.

Electrical measurement services should own only their electrical instruments.
They should read the latest cryostat state from the cryostat service instead of
opening iTC/iPS directly.

## Current MVP

The current implementation has three cryostat backends:

- `mock`: simulates the cryostat without hardware
- `mercury`: opens the configured iTC/iPS VISA resources and sends Mercury
  commands based on the current service configuration
- `heliox`: uses the abstract `HelioxX:HEL` interface for sample control while
  keeping VTI/gas access on the Mercury iTC side and field control on the
  system-global Mercury iPS

The same backend can be run in two operating modes:

- `read_only=true`: state and diagnostics are available, but command endpoints
  that would send `SET` commands are blocked
- `read_only=false`: command endpoints are enabled

The service currently:

- polls environmental state every second
- logs environmental state every 20 seconds by default
- exposes the latest state at `GET /state`
- exposes the active configuration at `GET /config`
- supports insert/profile changes at `POST /config/activate-insert`
- supports sample sensor preset changes at `POST /config/apply-sample-sensor`
- exposes recipe state and persistence endpoints under `/recipes*`
- exposes hardware diagnostics at:
  - `GET /diagnostics`
  - `GET /diagnostics/resources`
  - `GET /diagnostics/catalog`
  - `GET /diagnostics/readings`
  - `POST /diagnostics/query`
- streams state updates at `WS /ws/state`
- accepts basic commands:
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

## Run

Install service dependencies:

```bash
pip install -r requirements-service.txt
```

Start the lab read-only service:

```bash
python3 -m teslatron_services --config config/cryostat_lab_readonly.json --port 8765
```

Then open:

```text
http://127.0.0.1:8765/state
```

For live command tests, start the control service:

```bash
python3 -m teslatron_services --config config/cryostat_lab_control.json --port 8766
```

For Heliox read-only checks, prefer a separate port so it does not collide with
the Mercury control session:

```bash
python3 -m teslatron_services --config config/heliox_readonly.example.json --port 8767
```

## Example commands

Ramp the field to 3 T:

```bash
curl -X POST http://127.0.0.1:8766/commands/ramp-field \
  -H 'Content-Type: application/json' \
  -d '{"target_T": 3.0, "rate_T_per_min": 0.3}'
```

Ramp the temperature to 5 K:

```bash
curl -X POST http://127.0.0.1:8766/commands/ramp-temperature \
  -H 'Content-Type: application/json' \
  -d '{"target_K": 5.0, "rate_K_per_min": 1.0}'
```

Ramp only the sample loop:

```bash
curl -X POST http://127.0.0.1:8766/commands/temperature/sample/ramp \
  -H 'Content-Type: application/json' \
  -d '{"target_K": 5.0, "rate_K_per_min": 1.0}'
```

Ramp only the VTI loop:

```bash
curl -X POST http://127.0.0.1:8766/commands/temperature/vti/ramp \
  -H 'Content-Type: application/json' \
  -d '{"target_K": 5.0, "rate_K_per_min": 1.0}'
```

Set VTI needle valve opening directly:

```bash
curl -X POST http://127.0.0.1:8766/commands/vti/gas/set-needle \
  -H 'Content-Type: application/json' \
  -d '{"needle_valve_percent": 15.0}'
```

Set VTI pressure target:

```bash
curl -X POST http://127.0.0.1:8766/commands/vti/gas/set-pressure \
  -H 'Content-Type: application/json' \
  -d '{"pressure_mbar": 10.0}'
```

Ramp the field back to zero:

```bash
curl -X POST http://127.0.0.1:8766/commands/ramp-to-zero \
  -H 'Content-Type: application/json' \
  -d '{"rate_T_per_min": 0.3}'
```

Turn the persistent switch heater on:

```bash
curl -X POST http://127.0.0.1:8766/commands/ips/switch-heater \
  -H 'Content-Type: application/json' \
  -d '{"enabled": true}'
```

## Next step

The `mercury` backend intentionally preserves the working Mercury command
semantics of the instrument. To use it, set:

```json
{
  "cryostat": {
    "backend": "mercury",
    "read_only": true
  }
}
```

The configured Mercury modules are:

```json
{
  "itc": {
    "address": "TCPIP0::172.31.109.115::7020::SOCKET",
    "probe_signal": "DB8.T1",
    "probe_loop": "DB8.T1",
    "vti_signal": "MB1.T1",
    "vti_loop": "MB1.T1",
    "pressure": "DB5.P1"
  },
  "ips": {
    "address": "TCPIP0::172.31.109.116::7020::SOCKET",
    "magnet_group": "GRPZ",
    "magnet_temperature": "MB1.T1",
    "pt1_temperature": "DB8.T1",
    "pt2_temperature": "DB7.T1",
    "switch_on_delay_s": 300.0,
    "switch_off_delay_s": 300.0
  }
}
```

Use `backend=mock` for offline development and demos without hardware.
Use `backend=heliox` for the Heliox abstract sample-control model.
Keep `read_only=true` for the first hardware checks. In that mode,
`/state` and `/diagnostics/*` can read the instruments, while ramp/hold/abort
commands return HTTP 403 instead of sending `SET` commands.

## LabVIEW reference

The LabVIEW screenshots show an Ethernet setup with:

- iTC IP address: `172.31.109.115`
- iPS IP address: `172.31.109.116`
- iTC modules: `DB3.H1:HTR`, `DB4.G1:AUX`, `DB5.P1:PRES`,
  `DB8.T1:TEMP`, `MB0.H1:HTR`, `MB1.T1:TEMP`
- iPS modules: `DB7.T1:TEMP`, `DB8.T1:TEMP`, `GRPS:PSU`,
  `GRPX:PSU`, `GRPY:PSU`, `GRPZ:PSU`, `MB1.T1:TEMP`, `PSU.M1:PSU`

The service therefore uses `DB8.T1` as the sample/probe temperature channel and
`MB1.T1` as the VTI temperature channel. A previous software mapping used `MB0.H1` for
`get_probe_temp()`, but the LabVIEW module list labels `MB0.H1` as a heater.

An Ethernet Mercury example is provided at:

```text
config/cryostat_ethernet.example.json
```

For live lab checks, a ready-to-run read-only config is provided at:

```text
config/cryostat_lab_readonly.json
```

For live command sessions, use:

```text
config/cryostat_lab_control.json
```

First hardware checks should use read-only endpoints:

```text
GET /diagnostics/resources
GET /diagnostics/catalog
GET /diagnostics/readings
```

Start the service with:

```bash
python3 -m teslatron_services --config config/cryostat_lab_readonly.json --port 8765
```

The short operational workflow used in the lab is documented in:

```text
LAB_RUNBOOK.md
```

For one-off manual Mercury checks, use `POST /diagnostics/query`. It only
accepts commands beginning with `READ:`:

```bash
curl -X POST http://127.0.0.1:8765/diagnostics/query \
  -H 'Content-Type: application/json' \
  -d '{"target": "itc", "command": "READ:SYS:CAT"}'
```

VISA connection details are configurable per controller:

```json
{
  "address": "TCPIP0::172.31.109.115::7020::SOCKET",
  "timeout_ms": 3000,
  "read_termination": "\n",
  "write_termination": "\n"
}
```

Alternative TCP/IP VISA strings are collected in:

```text
config/cryostat_tcpip_candidates.json
```

## Heliox backend

Besides the standard Mercury backend, the service also supports a dedicated `heliox`
backend for controllers that expose the abstract `HelioxX:HEL` device described in the
Heliox manual.

Example configs:

```text
config/heliox_readonly.example.json
config/heliox_control.example.json
```

This backend deliberately uses the abstract Heliox interface for the sample path rather than
inferring sample semantics from raw `DBx` channels. The current implementation supports:
- sample temperature readback
- sample temperature setpoint read/write
- hold by reapplying the current Heliox sample temperature
- VTI loop and gas control through the configured Mercury iTC channels
- field control through the configured Mercury iPS

It intentionally does not support:
- direct sample fixed-heater control
- direct sample PID tuning

On 2026-05-11, the abstract Heliox commands were confirmed to respond on the live controller,
but the full service-level validation through the GUI/API remained pending when not in the lab.

## Heliox functional analysis

The current codebase is already structured around cryostat capabilities and insert profiles, so
the right question is not "which new backend do we need?" but "which existing functions stay the
same when the insert and iTC change?".

The working assumption, consistent with the Heliox manuals and with the current configuration
model, is:

- the `iPS` remains a global system component and should not change when the insert changes
- the `VTI` remains a system function even if the insert and the dedicated `iTC` change
- what may change with the insert is mainly the `iTC` address, the sample-related channels,
  thermometer wiring, and any extra diagnostic sensors

This is also reflected in the current config loader: insert profiles may override `itc`, while
attempting to override `ips` is rejected explicitly in
[config.py](/home/emiliano/Documents/Automazione/Teslatron_control-main/teslatron_services/cryostat/config.py:233).

### Functional matrix

| Function | Fisher / Mercury today | Expected for Heliox | Scope |
| --- | --- | --- | --- |
| Sample temperature readback | Raw Mercury `probe_signal` / `probe_loop` | Yes, possibly via `HelioxX:HEL` or sample-specific Mercury channels | Insert-specific |
| Sample setpoint / ramp / hold | Yes | Yes | Insert-specific |
| Sample sensor setup | Yes, through raw Mercury temperature device configuration | Likely different sensor mapping, possibly different calibration set | Insert-specific |
| Extra sample diagnostics | Raw `DBx/MBx` readings | Likely different set of sensors and UIDs | Insert-specific |
| VTI temperature readback | Yes | Should remain available | System-global |
| VTI control loop | Yes | Should remain available | System-global |
| VTI gas / needle / pressure control | Yes | Should remain available if the same VTI hardware is used | System-global |
| Magnetic field readback and control | Yes, via `iPS` | Unchanged | System-global |
| Switch heater / clamp / ramp-to-zero | Yes, via `iPS` | Unchanged | System-global |
| PID / fixed-heater sample control | Yes in raw Mercury mode | To verify on Heliox: may exist as raw Mercury loop functions even if `HelioxX` abstracts normal use | Likely insert-specific |

### Design consequence

This suggests a better reuse strategy than a fully separate "Heliox-only" control model:

1. Keep `IPS` as a global backend component.
2. Keep `VTI` and gas-control logic conceptually global.
3. Treat only the sample-side `iTC` mapping as insert-dependent.
4. Use `HelioxX:HEL` only where it adds value:
   sample setpoint, Heliox automation/state, and insert-specific semantics.
5. Continue to use ordinary Mercury device commands where the function is system-global and
   should not depend on the insert identity.

### Sample temperature control: Fisher vs Heliox

The most important behavioural difference is how sample temperature control is expressed at the
software boundary.

| Aspect | Fisher / Mercury | Heliox |
| --- | --- | --- |
| User-facing controlled variable | Sample temperature | `He3` pot / sample temperature |
| Readback path | Raw Mercury `probe_signal` / `probe_loop` | Abstract `HelioxX:HEL:SIG:TEMP` |
| Setpoint path | Raw Mercury loop target on `probe_loop` | Abstract `HelioxX:HEL:TSET` |
| Main low-temperature actuator | Sample loop heater | `He3` sorb heater |
| Main high-temperature actuator | Sample loop heater | `He3` pot heater |
| Control strategy owner | Service/backend plus Mercury loop primitives | Heliox firmware automation |
| Direct PID tuning | Exposed in backend and GUI | Not exposed in the Heliox backend |
| Direct fixed-heater mode | Exposed in backend and GUI | Not exposed in the Heliox backend |
| Actuator diagnostics | Sample loop heater state | Sorb and pot diagnostics (`SRBT`, `SRBH`, `SRBS`, `H3PH`) |

For Heliox, the backend does not implement the low-temperature versus high-temperature control
strategy itself. It sends the sample setpoint through `HelioxX:HEL:TSET` and lets the Heliox
firmware decide whether control should proceed via the `He3` sorb heater or via the `He3` pot
heater. The backend then reads the Heliox diagnostic signals to expose which physical actuator is
active.

### Practical next step

Before refactoring more code, the next technical step should be a command-level mapping table:

- current Fisher/Mercury command
- candidate Heliox raw Mercury command
- candidate `HelioxX` abstract command
- whether the function is `system-global` or `insert-specific`

That mapping should drive the implementation, rather than treating Heliox as a completely
independent cryostat model.

### Command mapping draft

The table below is the current working draft for that refactor. It is intentionally limited to
functions that matter for backend boundaries and reuse decisions.

| Function | Fisher / Mercury command today | Heliox raw Mercury candidate | HelioxX abstract candidate | Scope | Refactor note |
| --- | --- | --- | --- | --- | --- |
| Sample temperature readback | `READ:DEV:{probe_signal}:TEMP:SIG:TEMP?` | `READ:DEV:DB7.T1:TEMP:SIG:TEMP?` or `READ:DEV:DB8.T1:TEMP:SIG:TEMP?` depending on the configured pot sensor | `READ:DEV:HelioxX:HEL:SIG:TEMP` | Insert-specific | Keep user-facing sample readback abstract in Heliox. Raw pot channels remain useful for diagnostics. |
| Sample target readback | `READ:DEV:{probe_loop}:TEMP:LOOP:TSET?` | Raw pot loop target is not yet fixed in the current mapping | `READ:DEV:HelioxX:HEL:SIG:TSET` | Insert-specific | Prefer HelioxX for user-facing setpoint semantics. |
| Sample set target / ramp | `SET:DEV:{probe_loop}:TEMP:LOOP:TSET:{T}` | Raw pot loop write may exist, but it would bypass Heliox automation semantics | `SET:DEV:HelioxX:HEL:TSET:{T}` | Insert-specific | This should remain the primary Heliox control path. |
| Sample hold | Read current sample, then write probe loop target | Read current pot temperature, then write raw target | Read `SIG:TEMP`, then `SET:DEV:HelioxX:HEL:TSET:{current}` | Insert-specific | Hold should keep using the abstract Heliox path. |
| Sample heater diagnostic | `READ:DEV:{probe_loop}:TEMP:LOOP:HSET?` | `READ:DEV:DB2.H1:HTR:SIG:PERC?` or equivalent to verify on hardware | `READ:DEV:HelioxX:HEL:SIG:H3PH` | Insert-specific | In Heliox this is not the only relevant actuator. |
| Sorb temperature diagnostic | Not present in standard Fisher path | `READ:DEV:MB1.T1:TEMP:SIG:TEMP?` | `READ:DEV:HelioxX:HEL:SIG:SRBT` | Insert-specific | Important low-temperature control diagnostic. |
| Sorb heater diagnostic | Not present in standard Fisher path | `READ:DEV:MB0.H1:HTR:SIG:PERC?` or equivalent to verify on hardware | `READ:DEV:HelioxX:HEL:SIG:SRBH` | Insert-specific | Important low-temperature actuator diagnostic. |
| Sorb stable diagnostic | Not present in standard Fisher path | No raw equivalent identified yet | `READ:DEV:HelioxX:HEL:SIG:SRBS` | Insert-specific | Best treated as optional diagnostic data. |
| 1K / He4 plate temperature readback | Usually represented as VTI-side system temperature in the Fisher setup | `READ:DEV:DB6.T1:TEMP:SIG:TEMP?` | `READ:DEV:HelioxX:HEL:SIG:H4PT` | System-global in operation, Heliox-specific in naming | Good candidate for reuse in a shared VTI/system layer once verified live. |
| 1K plate stability | Not present in standard Fisher GUI path | No raw equivalent identified yet | `READ:DEV:HelioxX:HEL:SIG:H4PS` | System-global in operation | Optional Heliox diagnostic. |
| VTI pressure readback | `READ:DEV:{pressure}:PRES:SIG:PRES?` | `READ:DEV:DB3.P1:PRES:SIG:PRES?` | No dedicated HelioxX signal exposed in the current manual excerpt | System-global | This should stay in the shared Mercury/VTI path. |
| VTI pressure target | `READ:DEV:{pressure}:PRES:LOOP:PRST?` | `READ:DEV:DB3.P1:PRES:LOOP:PRST?` | Heliox parameters `NVHT`, `NVLT`, `NVCN` exist but are mode-specific control settings, not the generic live loop target | System-global | Do not replace the normal pressure loop semantics casually. |
| VTI pressure control set | `SET:DEV:{pressure}:PRES:LOOP:PRST:{P}` | `SET:DEV:DB3.P1:PRES:LOOP:PRST:{P}` | Possibly `SET:DEV:HelioxX:HEL:NVHT/NVLT/NVCN:{P}` depending on operating mode | System-global | The shared path is still the safer architectural default. |
| VTI needle readback | `READ:DEV:{pressure}:PRES:LOOP:FSET?` | `READ:DEV:DB3.P1:PRES:LOOP:FSET?` or direct `DB4.G1` read if needed | No direct HelioxX live signal in the current excerpt | System-global | Shared Mercury path should likely remain authoritative. |
| VTI needle set | `SET:DEV:{pressure}:PRES:LOOP:FSET:{x}` | `SET:DEV:DB3.P1:PRES:LOOP:FSET:{x}` or direct `DB4.G1` command if required | Heliox firmware controls the needle indirectly in some modes | System-global | Shared path should remain until a strong reason appears to abstract it. |
| Field readback | `READ:DEV:{ips.field_channel}:PSU:SIG:FLD?` | Same | None | System-global | No Heliox-specific change expected. |
| Field ramp | `SET:DEV:{ips.field_channel}:PSU:SIG:FSET:{T}` with switch-heater logic around it | Same | None | System-global | Must remain outside the sample strategy. |
| Ramp to zero | Mercury iPS zeroing path | Same | None | System-global | Same as above. |
| Clamp | Mercury iPS clamp path | Same | None | System-global | Same as above. |
| Switch heater read / write | Mercury iPS `SWHT` / related checked logic | Same | None | System-global | Unaffected by insert identity. |

### Refactor implication from the mapping

This draft suggests a three-layer split:

1. `GlobalFieldControl`
   owns `iPS`, switch heater, field ramps, clamp, and zeroing.
2. `GlobalVtiControl`
   owns VTI temperature, pressure, and needle-valve behaviour through the ordinary Mercury paths.
3. `SampleControlStrategy`
   switches between:
   - raw Mercury sample loop control for Fisher-style inserts
   - abstract `HelioxX:HEL` sample control for Heliox inserts

The practical consequence is that the current standalone `heliox` backend is likely an
intermediate implementation, not the final architecture.

### Manual config-derived device mapping

The repository now also contains a live-style Heliox Cryosys configuration in
[docs/manuals/!cryosys_Heliox.cfg!](/home/emiliano/Documents/Automazione/Teslatron_control-main/docs/manuals/!cryosys_Heliox.cfg!:1)
plus detailed `dev_cfg` fragments. Those files confirm the following raw device associations:

- `MB1.T1` = `He3` sorb sensor
- `MB0.H1` = `He3` sorb heater
- `DB8.T1` = `He3` pot low sensor
- `DB7.T1` = `He3` pot high sensor
- `DB2.H1` = `He3` pot heater
- `DB6.T1` = `He4` pot / VTI-side temperature sensor
- `DB1.H1` = `He4` pot heater
- `DB3.P1` = pressure sensor
- `DB4.G1` = needle valve / VTI flow actuator

This is strong evidence that the Heliox example configs should not inherit the default
Mercury `VTI` mapping (`MB1.T1`), because on this system `MB1.T1` is the sorb, not the VTI.

### HelioxX template thresholds

The file
[docs/manuals/HelioxX_cfg](/home/emiliano/Documents/Automazione/Teslatron_control-main/docs/manuals/HelioxX_cfg:1)
adds useful controller-level semantics even though it does not define new hardware UIDs.
In particular it records the HelioxX operating thresholds used by the abstract controller:

- `CMODE_XOVER = 1.6000 K`
- `CONDENSED_TEMP = 1.5900 K`
- `He3_SORB_COLD = 1.8000 K`
- `He3_SORB_HT_CONTR = 15.0000 K`
- `He3_SORB_REGEN = 35.0000 K`
- `He3_SORB_OUTGAS = 50.0000 K`
- `He3_POT_BOILOFF = 5.0000 K`
- `POT_EMPTY = 2.0000 K`
- `OPT_NV_LT = 5.0000 mB`
- `OPT_NV_HT = 10.0000 mB`
- `OPT_NV_RCON = 6.0000 mB`
- `OPT_NV_CLOSE = 0.1000 mB`

These values are useful as diagnostic metadata and for interpreting transitions such as
low-temperature versus high-temperature control, regeneration, and needle-valve presets.
They should not automatically be treated as backend control rules unless verified live against
the actual Heliox firmware behaviour.

## Session ownership note

The Mercury controllers should have a single active client owner during testing. On
2026-05-11, the iPS at `TCPIP::172.31.109.116::7020::SOCKET` rejected Python read queries
with `Connection reset by peer` while the LabVIEW VI still held the session open. As soon as
the VI disconnected, the same Python `READ:` and `*IDN?` queries succeeded. If a controller
appears reachable but resets immediately, first verify that LabVIEW or another VISA client is
not still connected.
