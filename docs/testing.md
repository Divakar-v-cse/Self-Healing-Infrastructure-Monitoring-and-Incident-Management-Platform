# Testing guide

## Automated suite

```bash
cd backend
python -m pytest tests -q          # 85 tests
python -m pytest tests -v          # per-test detail
python -m pytest tests/test_safeguards.py -v
```

Tests configure the environment **before** importing the app (`tests/conftest.py`), so the cached settings
never point at a developer database or a live Prometheus. Each test gets a fresh schema; the suite makes no
network calls.

| File | Tests | Focus |
| --- | --- | --- |
| `test_classifier.py` | 17 | exact alert table, keyword fallback, severity normalisation, inverse comparators, runbooks |
| `test_safeguards.py` | 16 | every rule, boundary cases, force semantics, blocked rows excluded from limits |
| `test_detector.py` | 14 | breach detection, dedupe, sustained-duration gating, auto-resolve, Prometheus failure, host-down, sample persistence, retry policy, cross-path convergence |
| `test_recovery.py` | 12 | dry-run default, `--limit` targeting, path traversal, missing inventory, orchestration, cooldown, in-flight guard |
| `test_api.py` | 36 | health/ready, hosts CRUD, metrics, thresholds validation, safeguards, incident lifecycle, manual recovery, webhook ingest + resolution, overview, reports, events, `/metrics`, simulation error paths |

### What the fakes look like

- `FakePrometheus` maps the canonical PromQL strings to canned samples — a query the detector does not send
  raises `AssertionError`, so an unnoticed query change fails loudly.
- `AnsibleRunner` runs in dry-run mode: it resolves the playbook path, validates it exists, builds the exact
  command and returns `simulated`. No SSH, no external binary.

## Configuration validation

```bash
promtool check config monitoring/prometheus/prometheus.yml
promtool check rules  monitoring/prometheus/rules/infrastructure.yml
amtool check-config   monitoring/alertmanager/alertmanager.yml
docker compose config --quiet
```

These run in CI on every push (`.github/workflows/ci.yml`), together with `ansible-playbook --syntax-check`
for all playbooks.

## Reproducing the end-to-end scenarios

### 1. CPU saturation → alert → incident → recovery → auto-resolve

```bash
docker compose up -d --build
python scripts/simulate_failure.py --host web-01 --metric cpu --value 97 --duration 300 --watch
```

Expected timeline:

| Time | Event |
| --- | --- |
| T+0 | fault injected; CPU climbs as the 5m rate window fills |
| ~T+2m | `CpuSaturation` fires (2m `for:` window) |
| ~T+2m | Alertmanager posts; incident opened `critical`, `metric=cpu`; auto-recovery `simulated` |
| T+3m | a second attempt is **blocked** by `cooldown` |
| T+5m | fault expires; CPU returns to ~20%; incident resolves after the settle window; MTTR recorded |

### 2. Host down → availability incident

```bash
python scripts/simulate_failure.py --host db-01 --metric down --duration 120 --watch
```

`/metrics/db-01` returns `503`, so `up{node="db-01"} == 0` and `HostDown` fires after 1m.

### 3. Prove the safeguards

```bash
# after an incident exists
curl -s -X POST localhost:8000/api/v1/incidents/<id>/recover \
     -H 'Content-Type: application/json' -d '{}' | jq
# => {"allowed": false, "rule": "cooldown", "reason": "cooldown active for 'cpu' on web-01; retry in 275s"}
```

Then trip the circuit breaker:

```bash
for i in 1 2 3; do
  curl -s -X POST localhost:8000/api/v1/incidents/<id>/recover \
       -H 'Content-Type: application/json' -d '{"force": true}'
done
```

With `SHIP_ANSIBLE_DRY_RUN=false` and an unreachable host, three `failed` actions open the breaker and the
fourth attempt returns `rule=circuit_breaker`.

### 4. Threshold tuning

```bash
curl -s localhost:8000/api/v1/thresholds | jq
curl -s -X PATCH localhost:8000/api/v1/thresholds/cpu \
     -H 'Content-Type: application/json' -d '{"warning": 70, "critical": 85, "duration_seconds": 60}'
```

A `warning >= critical` pair for a `>=` comparator is rejected with `422`.

## CI

`.github/workflows/ci.yml` runs three jobs:

1. **backend** — pytest on Python 3.11 and 3.12.
2. **frontend** — `npm install`, `tsc -b`, `vite build`.
3. **configs** — promtool, amtool, `docker compose config`, Ansible syntax check.

## Manual QA checklist

- [ ] Console loads and shows three hosts with live meters.
- [ ] Incident drawer shows classification reason, runbook steps and recovery timeline.
- [ ] `simulated` actions are visually distinct from `success`.
- [ ] Maintenance mode stops automated recovery for that host.
- [ ] Threshold edit propagates to the next detection cycle.
- [ ] Grafana dashboard renders with `node` variable populated.
- [ ] `/metrics` exposes `ship_incidents_total` and `ship_recovery_actions_total`.
