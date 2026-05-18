# Electrical measurement outputs

Electrical measurements are stored in two parallel formats.

## JSONL event log

The JSONL stream remains the compatibility and machine-log output. Each line contains the
raw event envelope with:

- absolute timestamp
- `run_id`
- `plan_id`
- instrument name
- measurement payload
- cryostat summary

This file is still useful for service debugging, replay, and future extensions where the
event shape may carry more context than a flat table.

## Tabular CSV for analysis

Each simple electrical run also writes a dedicated CSV intended for direct import into
Origin, Python, MATLAB, Excel, or Igor:

```text
data/electrical/YYYY-MM-DD/<run_id>/<run_id>_electrical.csv
```

Each row represents one electrical measurement.

Mandatory columns:

- `run_id`
- `plan_id`
- `instrument`
- `timestamp_unix_s`
- `timestamp_iso`
- `time_relative_s`
- `sample_temperature_K`
- `field_T`
- `safe_to_measure`

Additional cryostat columns are included when present:

- `vti_temperature_K`
- `pressure_mbar`
- `cryostat_timestamp`

The instrument payload is flattened into extra columns:

- flat dictionaries are written directly
- nested dictionaries are flattened with `_` as separator
- non-scalar values are serialized as compact JSON strings
- if a later measurement introduces a new key, the CSV header is rewritten to add the new column

`timestamp_iso` is written in UTC using ISO 8601 with `Z`.
`time_relative_s` is computed from `time.monotonic()` at run start, not from wall-clock time.

Example:

```csv
run_id,plan_id,instrument,timestamp_unix_s,timestamp_iso,time_relative_s,sample_temperature_K,field_T,safe_to_measure,current_A,voltage_V,resistance_ohm
test_run,periodic,mock_meter,1710000000.123,2024-03-09T10:00:00.123Z,0.532,4.21,1.5,True,1.0e-06,0.0021,2100
```

## Continuous measurements during ramps with LabVIEW

LabVIEW or another external acquisition program can measure during temperature or field ramps by
polling the cryostat service `GET /measurement-context` endpoint while the recipe continues running.
The endpoint is intentionally lightweight, read-only, and based on the service state cache so it is
safe to poll at roughly 1-5 Hz from LabVIEW.

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

Use explicit JSON field names such as `sample_temperature_K` and `field_T`. Do not use an anonymous
array like `[T, B]`, because that is fragile and harder to evolve safely across LabVIEW and Python
code.

For stable-point measurements, recipes can use an `external_measurement` step with `mode: "point"`
so the cryostat workflow pauses until LabVIEW confirms completion. For continuous acquisitions,
recipes can use `mode: "start"` before a ramp and `mode: "stop"` after the ramp. In that pattern,
LabVIEW starts acquisition, the cryostat continues the ramp, and LabVIEW correlates the acquired
data with `timestamp_unix_s` or `timestamp_iso`.

HTTP polling is adequate for slower acquisitions around 1-5 Hz. For faster acquisitions, the
recommended approach is to record timestamps on both sides and merge data offline. If tighter live
coupling becomes necessary later, a streaming interface such as WebSocket or Server-Sent Events can
be added in a future revision.
