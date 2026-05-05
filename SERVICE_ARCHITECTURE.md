# Teslatron service architecture

This is the first service-oriented layer for the Teslatron controller. The
original notebook/script API is intentionally left unchanged.

## Roles

The cryostat service owns the cryostat hardware:

- Mercury iTC
- Mercury iPS
- environmental state such as temperature, field, pressure, heaters

Electrical measurement services should own only their electrical instruments.
They should read the latest cryostat state from the cryostat service instead of
opening iTC/iPS directly.

## Current MVP

The current implementation has two cryostat backends:

- `mock`: simulates the cryostat without hardware
- `mercury`: opens the configured iTC/iPS VISA resources and sends Mercury
  commands based on the old project
- `read_only`: when true, state and diagnostics are available but command
  endpoints that would send `SET` commands are blocked

- polls environmental state every second
- logs environmental state every 20 seconds by default
- exposes the latest state at `GET /state`
- exposes the active configuration at `GET /config`
- exposes hardware diagnostics at:
  - `GET /diagnostics`
  - `GET /diagnostics/resources`
  - `GET /diagnostics/catalog`
  - `GET /diagnostics/readings`
- streams state updates at `WS /ws/state`
- accepts basic commands:
  - `POST /commands/ramp-temperature`
  - `POST /commands/ramp-field`
  - `POST /commands/hold`
  - `POST /commands/abort`

## Run

Install service dependencies:

```bash
pip install -r requirements-service.txt
```

Start the mock cryostat service:

```bash
python3 -m teslatron_services --config config/cryostat.json --port 8765
```

Then open:

```text
http://127.0.0.1:8765/state
```

## Example commands

Ramp the field to 3 T in mock mode:

```bash
curl -X POST http://127.0.0.1:8765/commands/ramp-field \
  -H 'Content-Type: application/json' \
  -d '{"target_T": 3.0, "rate_T_per_min": 0.3}'
```

Ramp the temperature to 5 K in mock mode:

```bash
curl -X POST http://127.0.0.1:8765/commands/ramp-temperature \
  -H 'Content-Type: application/json' \
  -d '{"target_K": 5.0, "rate_K_per_min": 1.0}'
```

## Next step

The `mercury` backend is intentionally close to the old `instruments.py`
commands. To use it, set:

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
    "address": "ASRL7::INSTR",
    "probe_signal": "DB8.T1",
    "probe_loop": "DB8.T1",
    "vti_signal": "MB1.T1",
    "vti_loop": "MB1.T1",
    "pressure": "DB5.P1"
  },
  "ips": {
    "address": "ASRL8::INSTR",
    "magnet_group": "GRPZ"
  }
}
```

Keep `backend` as `mock` until the real iTC/iPS are connected and ready.
Keep `read_only` as `true` for the first hardware checks. In that mode,
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
`MB1.T1` as the VTI temperature channel. The old project used `MB0.H1` for
`get_probe_temp()`, but the LabVIEW module list labels `MB0.H1` as a heater.

An Ethernet Mercury example is provided at:

```text
config/cryostat_ethernet.example.json
```

First hardware checks should use read-only endpoints:

```text
GET /diagnostics/resources
GET /diagnostics/catalog
GET /diagnostics/readings
```
