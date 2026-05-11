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

## 3. First live checks

- Confirm the GUI loads and keeps updating.
- Confirm ITC sample temperature, VTI temperature, pressure, IPS field, current, and voltage look sensible.
- If the iPS suddenly stops replying or resets connections, check whether LabVIEW or another VISA client is still connected.

## 4. Safe command order

Use the smallest possible steps when testing live control:

1. Verify readback only.
2. Send `Hold`.
3. Test a small temperature ramp.
4. Test a small field ramp.

Do not start with a large ramp or multiple simultaneous changes.

## 5. Commands already validated in the lab

On 2026-05-11, the following were confirmed on the live system:

- GUI readback from both iTC and iPS
- `Hold`
- Temperature ramp
- Field ramp

## 6. If something looks wrong

- Stop sending further commands.
- Check whether the wrong config is loaded.
- Check whether LabVIEW is still connected.
- Return to the read-only config if you want to inspect state without risk.

## 7. Recommended working habit

- Use LabVIEW or Python for a given live session, not both at once.
- Keep the read-only config as the default inspection mode.
- Switch to the control config only when you are ready to send commands.

## 8. Heliox notes

- For Heliox, use:

```text
config/heliox_readonly.example.json
config/heliox_control.example.json
```

- The Heliox backend controls the sample through the abstract device `HelioxX:HEL`.
- VTI loop and gas control are still expected to come from the underlying Mercury iTC mapping.
- Field control is still expected to come from the system-global Mercury iPS.
- Direct sample PID/fixed-heater control is intentionally not exposed on Heliox.
- As of 2026-05-11, the backend logic is implemented and locally tested, but the full GUI/API
  validation path should still be re-checked in the lab before routine use.
