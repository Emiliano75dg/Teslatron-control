# Teslatron Lab Runbook

This note captures the shortest safe workflow for live cryostat control on the
Teslatron system of the Q-MAT lab, within CNR-SPIN and the Department of
Physics "E. Pancini" of the University of Naples Federico II.

## 1. Before connecting

- Make sure LabVIEW is disconnected from the Mercury controllers before starting the Python service.
- Do not keep LabVIEW and Python connected to the same iTC or iPS session at the same time.
- For first checks, prefer the read-only config:

```text
config/cryostat_lab_readonly.json
```

- For live commands, use the control config:

```text
config/cryostat_lab_control.json
```

## 2. Start the service

Read-only:

```bash
python3 -m teslatron_services --config config/cryostat_lab_readonly.json --port 8765
```

Control enabled:

```bash
python3 -m teslatron_services --config config/cryostat_lab_control.json --port 8766
```

Open the GUI:

```text
http://127.0.0.1:8765   (read-only)
http://127.0.0.1:8766   (control)
```

## 3. Safe shutdown and port release

- Closing only the browser tab does not stop the service.
- To stop the service cleanly, use one of these:
  - press `Ctrl+C` in the terminal that started `python3 -m teslatron_services ...`
  - click `Shutdown service` in the GUI Commands tab
- Both paths stop Uvicorn cleanly, run the FastAPI lifespan shutdown, call `CryostatService.stop()`,
  and close the active backend connection before the port is released.
- To confirm the port is free after shutdown:

```bash
ss -tanp | grep 8765
ss -tanp | grep 8766
ss -tanp | grep 8767
```

- If the command returns no matching line, the port is no longer in use.

## 4. First live checks

- Confirm the GUI loads and keeps updating.
- Confirm ITC sample temperature, VTI temperature, pressure, IPS field, current, and voltage look sensible.
- If the iPS suddenly stops replying or resets connections, check whether LabVIEW or another VISA client is still connected.

## 5. Safe command order

Use the smallest possible steps when testing live control:

1. Verify readback only.
2. Send `Hold`.
3. Test a small temperature ramp.
4. Test a small field ramp.

Do not start with a large ramp or multiple simultaneous changes.

## 6. Commands already validated in the lab

On 2026-05-11, the following were confirmed on the live system:

- GUI readback from both iTC and iPS
- `Hold`
- Temperature ramp
- Field ramp

## 7. If something looks wrong

- Stop sending further commands.
- Check whether the wrong config is loaded.
- Check whether LabVIEW is still connected.
- Return to the read-only config if you want to inspect state without risk.

## 8. Recommended working habit

- Use LabVIEW or Python for a given live session, not both at once.
- Keep the read-only config as the default inspection mode.
- Switch to the control config only when you are ready to send commands.

## 9. LabVIEW external-measurement handshake

For external electrical measurements coordinated by LabVIEW, prefer the new
HTTP handshake instead of parsing the full cryostat state ad hoc.

Recommended endpoints:

- `GET /measurement-context` for lightweight cryostat polling during acquisition
- `GET /external-measurements/pending` for pending recipe requests
- `POST /external-measurements/complete` to acknowledge `completed` or `failed`
- `POST /recipes/signal` when a direct signal-based flow is preferred

Recommended polling rate for LabVIEW is about 1-5 Hz for slow acquisitions.
The payload uses explicit field names such as `sample_temperature_K` and
`field_T`; do not depend on anonymous array ordering such as `[T, B]`.
LabVIEW can poll these endpoints continuously: when no external measurement is
active, `GET /external-measurements/pending` returns `{"pending": false}`,
while `GET /measurement-context` still returns the latest available context.

## 10. Heliox notes

- For Heliox, use:

```text
config/heliox_readonly.example.json
config/heliox_control.example.json
config/heliox_local_gui.example.json
```

- Heliox requires a dedicated service started with `backend: "heliox"` in the
  selected config file.
- The standard configuration and the Heliox configuration are separate service
  modes.
- Changing insert profile inside the standard service does not switch the
  backend to Heliox; it only updates the active insert/profile mapping within
  the standard configuration.
- In practice, the standard configuration is for Fisher probe or Basic probe,
  while the Heliox configuration is for the Heliox probe only.
- Legacy configs that still say `backend: "mercury"` are accepted and treated
  as `backend: "standard"`.
- The Heliox backend controls the sample through the abstract device `HelioxX:HEL`.
- VTI loop and gas control are still expected to come from the underlying Mercury iTC mapping.
- Field control is still expected to come from the system-global Mercury iPS.
- Direct sample PID/fixed-heater control is intentionally not exposed on Heliox.
- The current live Heliox iTC address validated on 2026-05-12 is `172.31.109.137:7020`.
- The current live Mercury iPS address validated on 2026-05-12 is `172.31.109.116:7020`.
- The local docs under `docs/manuals` support the current Heliox raw mapping:
  `DB8.T1` = `He3` pot low, `DB7.T1` = `He3` pot high, `DB6.T1` = `He4` / VTI side,
  `MB1.T1` = `He3` sorb, `DB3.P1` = pressure, `DB4.G1` = needle valve.
- Recommended example startup:

```bash
python3 -m teslatron_services --config config/heliox_readonly.example.json --port 8767
```

- For local GUI checks without Heliox hardware, use:

```bash
python3 -m teslatron_services --config config/heliox_local_gui.example.json --port 8767
```

- This local GUI config is writable on purpose so dropdowns and command forms stay usable during frontend checks.
- It remains safe because all ITC/IPS addresses are redirected to local loopback ports with no real hardware behind them.

- As of 2026-05-12, the live GUI/API path was re-validated in the lab.
- A service lifecycle bug was also identified and fixed: the `CryostatService` must be created
  inside the FastAPI lifespan, not before server startup, otherwise live controller polling may
  fail with intermittent `Connection reset by peer` errors.
