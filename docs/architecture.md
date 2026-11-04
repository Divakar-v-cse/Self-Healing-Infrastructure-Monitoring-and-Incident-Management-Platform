# Architecture

## Design principles

1. **Two detection paths, one truth.** Prometheus rules know about `for:` durations and routing; the backend
   knows about thresholds stored in the database. Both are kept, because production teams need both — but
   they must not create duplicate incidents. The shared `threshold|host|metric` fingerprint makes the
   Alertmanager path and the detector path converge.
2. **Automation must be able to refuse.** Recovery is gated by an explicit safeguard engine whose rules are
   persisted with every blocked attempt. A blocked action is a first-class outcome, not an error.
3. **Honest status.** Dry-run recoveries are stored as `simulated`, never `success`. The UI surfaces this
   distinction instead of hiding it.
4. **Dependency injection over globals.** The detector takes a Prometheus client and an Ansible runner, so
   tests exercise real logic with fake I/O instead of mocking the code under test.

## Components

### Simulator (`simulator/app.py`)

A ~300-line stdlib HTTP service that emits the node_exporter metric contract for three virtual hosts and
supports fault injection:

| Endpoint | Purpose |
| --- | --- |
| `GET /metrics` | All hosts (human/debug use) |
| `GET /metrics/{host}` | One host; returns **503 when the host is "down"** so `up == 0` |
| `POST /sim/faults` | `{hostname, metric, value, duration_seconds}`; `metric=down` takes the host offline |
| `DELETE /sim/faults` | Clear all faults (or `?hostname=`) |

Counters (`node_cpu_seconds_total`, `node_network_*_bytes_total`) advance with wall-clock time between
scrapes, so `rate()` behaves like a real exporter. Prometheus overrides `instance` per target and injects
`node`, so the virtual hosts look like distinct machines on the same port.

### Prometheus (`monitoring/prometheus/`)

- `prometheus.yml` — four jobs: `prometheus`, `node` (3 simulated hosts, `__metrics_path__` per host),
  `blackbox` (synthetic HTTP probes), `ship-backend` (platform KPIs).
- `rules/infrastructure.yml` — 13 rules in three groups: `host-availability`, `host-resources`,
  `platform-health`. Every alert carries `severity`, `category`, `team`, a `metric` label (used for
  fingerprinting) and a rendered `value` annotation.

### Alertmanager (`monitoring/alertmanager/alertmanager.yml`)

Routes by severity (critical: `group_wait 5s`, repeat 1h), groups by `alertname,node,category`, posts to the
backend webhook with `send_resolved: true`, and declares four inhibit rules so a dead host does not also page
for "disk full" and a critical CPU alert suppresses its own warning.

### Backend (`backend/app/`)

```
config.py        pydantic-settings, SHIP_* env prefix, safe defaults
database.py      engine/session factory, SQLite WAL pragmas, test reset hook
models.py        Host, MetricSample, Alert, Incident, RecoveryAction, Threshold, HealthReport, EventLog
schemas.py       request/response contracts (pydantic v2)
services/
  prometheus.py  HTTP API client + canonical PromQL map + metric labels
  classifier.py  alert/threshold classification, severity normalisation, breach_level()
  detector.py    scrape → persist samples → evaluate thresholds → open/refresh/auto-resolve incidents
  incidents.py   dedupe, lifecycle, Alertmanager ingestion, canonical fingerprints
  recovery.py    AnsibleRunner + trigger_recovery orchestration
  safeguards.py  pure decision function over DB state (9 rules)
  reports.py     health snapshots and dashboard aggregation
  events.py      append-only audit trail
api/             hosts, incidents, alerts/webhook/recoveries, system (overview/config/reports/simulation)
workers/         asyncio loop, 30s cadence, each cycle in a thread with its own session
```

**Detector cycle.** For each `up{job="node"}` series it upserts the host, records five metric samples, derives
host status (`up`/`degraded`/`down`), then evaluates every enabled threshold. A breach must be sustained for
`duration_seconds` (tracked in memory per `(host, metric)`); then an incident is opened or refreshed.
Incidents whose metric no longer breaches are resolved once they are older than the settle window, and MTTR
is stored. Samples older than `metric_retention_days` are pruned.

**Recovery orchestration.** `trigger_recovery` refuses when an action is already running for the incident,
asks `safeguards.evaluate`, persists a `blocked` row on refusal, otherwise marks the incident `recovering`,
runs the playbook synchronously (API calls run in the threadpool, so the event loop is never blocked) and
records status, output, command line and duration.

### Frontend (`frontend/src/`)

React 18 + TypeScript, no UI or chart library. `usePolling` refreshes each view on an interval while keeping
the previous payload so the UI does not flicker. Charts are hand-built SVG (`Sparkline`, `Chart`) to keep the
bundle at ~195 kB.

| View | Content |
| --- | --- |
| Overview | 4 KPIs, host tiles with live utilisation meters, active incidents, alert stream, recovery activity |
| Incidents | Filterable queue (status/severity/search), pagination, ack/resolve/recover, detail drawer with runbook + recovery timeline |
| Hosts | Inventory table with per-host metrics, maintenance and auto-recovery switches |
| Recoveries | Every action including blocked ones, expandable Ansible output, success rate |
| Reports | Generated health snapshots, per-host breakdown, category mix, top incidents |
| Settings | Editable thresholds, safeguard limits, dry-run visibility |
| Simulation | Fault injection, virtual host telemetry, active faults, one-click detection cycle |

## Data model

```
Host 1──* MetricSample
Host 1──* Incident 1──* RecoveryAction
Host 1──* Alert (nullable host)
Threshold (one row per metric)
HealthReport (JSON payload with per-host + per-category breakdown)
EventLog (append-only, feeds the activity stream)
```

`Incident.status`: `open → acknowledged → recovering → resolved` (or `suppressed`).
`RecoveryAction.status`: `pending → running → success | failed | timeout | simulated`, or `blocked`.

## Failure modes and behaviour

| Failure | Behaviour |
| --- | --- |
| Prometheus unreachable | Detection cycle logs a warning event, returns an error summary; API stays up; `/ready` reports `prometheus: false` |
| Alertmanager unreachable | Prometheus keeps evaluating; alerts are buffered by Alertmanager when it returns; the detector path still opens incidents |
| Ansible binary missing | Action recorded as `failed` with `ansible binary not found` |
| Ansible host unreachable | Playbook exits non-zero → `failed`; after N consecutive failures the circuit breaker opens |
| Backend restart mid-recovery | An action left as `running` blocks new attempts for that incident until resolved manually — deliberate, to avoid double remediation |
| Database unavailable | FastAPI fails readiness; the process does not silently continue |
| Duplicate webhook delivery | Fingerprint dedupe refreshes the existing incident instead of opening a new one |

## Scaling notes

- The detection loop is single-process. For horizontal scale, run the API read-only and move detection to a
  cron/sidecar (`scripts/detect_incidents.py --json`) with a distributed lock, or elect one worker.
- SQLite handles a lab comfortably; for production use Postgres (`SHIP_DATABASE_URL`) and add an index
  migration strategy (Alembic is not yet wired).
- Metric samples are pruned by age; for long retention, keep the raw series in Prometheus and store only
  aggregates in the platform database.
