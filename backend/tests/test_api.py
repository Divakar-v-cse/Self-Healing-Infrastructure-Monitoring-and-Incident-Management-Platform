"""HTTP API contract tests."""

from __future__ import annotations

from app.models import Host, Incident

API = "/api/v1"


def test_health(client):
    response = client.get(f"{API}/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "self-healing" in body["service"].lower()


def test_readiness_reports_each_dependency(client):
    response = client.get(f"{API}/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert set(body["checks"]) == {"database", "prometheus", "simulator"}
    assert body["checks"]["database"] is True


def test_openapi_schema_is_valid(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    for expected in (
        f"{API}/hosts",
        f"{API}/incidents",
        f"{API}/overview",
        f"{API}/thresholds",
        f"{API}/safeguards",
        f"{API}/webhooks/alertmanager",
    ):
        assert expected in paths


def test_hosts_are_seeded(client):
    response = client.get(f"{API}/hosts")
    assert response.status_code == 200
    hosts = response.json()
    assert {host["hostname"] for host in hosts} == {"web-01", "api-01", "db-01"}
    assert all("latest_metrics" in host for host in hosts)


def test_create_and_update_host(client):
    created = client.post(
        f"{API}/hosts",
        json={"hostname": "cache-01", "instance": "10.10.0.14:9100", "role": "cache", "labels": {"env": "lab"}},
    )
    assert created.status_code == 201
    host = created.json()
    assert host["hostname"] == "cache-01"
    assert host["status"] == "unknown"

    duplicate = client.post(f"{API}/hosts", json={"hostname": "cache-01"})
    assert duplicate.status_code == 409

    patched = client.patch(f"{API}/hosts/{host['id']}", json={"maintenance_mode": True, "role": "redis"})
    assert patched.status_code == 200
    assert patched.json()["maintenance_mode"] is True
    assert patched.json()["role"] == "redis"

    missing = client.patch(f"{API}/hosts/999999", json={"role": "x"})
    assert missing.status_code == 404


def test_host_metrics_shape(client):
    hosts = client.get(f"{API}/hosts").json()
    response = client.get(f"{API}/hosts/{hosts[0]['id']}/metrics?minutes=30")
    assert response.status_code == 200
    series = response.json()
    assert {item["metric"] for item in series} == {"cpu", "memory", "disk", "network", "availability"}
    assert all(isinstance(item["points"], list) for item in series)


def test_thresholds_are_seeded_and_configurable(client):
    response = client.get(f"{API}/thresholds")
    assert response.status_code == 200
    thresholds = response.json()
    assert len(thresholds) == 5
    cpu = next(item for item in thresholds if item["metric"] == "cpu")
    assert cpu["warning"] == 75.0 and cpu["critical"] == 90.0

    updated = client.patch(f"{API}/thresholds/cpu", json={"warning": 70.0, "critical": 85.0, "duration_seconds": 120})
    assert updated.status_code == 200
    assert updated.json()["warning"] == 70.0

    invalid = client.patch(f"{API}/thresholds/cpu", json={"warning": 99.0, "critical": 50.0})
    assert invalid.status_code == 422

    missing = client.patch(f"{API}/thresholds/nonexistent", json={"warning": 10.0})
    assert missing.status_code == 404


def test_safeguards_are_readable_and_patchable(client):
    response = client.get(f"{API}/safeguards")
    assert response.status_code == 200
    config = response.json()
    assert config["max_attempts_per_incident"] == 3
    assert config["ansible_dry_run"] is True

    patched = client.patch(f"{API}/safeguards", json={**config, "recovery_cooldown_seconds": 120})
    assert patched.status_code == 200
    assert patched.json()["recovery_cooldown_seconds"] == 120


def test_incident_lifecycle(client):
    created = client.post(
        f"{API}/incidents",
        json={
            "title": "Disk pressure on db-01",
            "hostname": "db-01",
            "category": "disk",
            "severity": "critical",
            "metric": "disk",
            "observed_value": 94.5,
            "threshold_value": 90.0,
        },
    )
    assert created.status_code == 201
    incident = created.json()
    assert incident["reference"].startswith("INC-")
    assert incident["hostname"] == "db-01"
    assert incident["status"] == "open"

    listing = client.get(f"{API}/incidents?status=active")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    acknowledged = client.patch(f"{API}/incidents/{incident['id']}", json={"status": "acknowledged"})
    assert acknowledged.status_code == 200
    assert acknowledged.json()["status"] == "acknowledged"

    resolved = client.patch(f"{API}/incidents/{incident['id']}", json={"status": "resolved"})
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"
    assert resolved.json()["mttr_seconds"] is not None


def test_manual_recovery_is_recorded(client):
    created = client.post(
        f"{API}/incidents",
        json={"title": "CPU saturation on web-01", "hostname": "web-01", "category": "cpu", "severity": "critical"},
    ).json()

    response = client.post(f"{API}/incidents/{created['id']}/recover", json={"reason": "operator test"})
    assert response.status_code == 200
    body = response.json()
    assert body["allowed"] is True
    assert body["status"] == "simulated"
    assert body["action"]["dry_run"] is True

    second = client.post(f"{API}/incidents/{created['id']}/recover", json={})
    assert second.status_code == 200
    assert second.json()["allowed"] is False
    assert second.json()["rule"] == "cooldown"

    history = client.get(f"{API}/incidents/{created['id']}/recoveries").json()
    assert len(history) == 2


def test_alertmanager_webhook_opens_incident(client):
    payload = {
        "version": "4",
        "status": "firing",
        "receiver": "platform-webhook",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "HostDiskFull",
                    "severity": "critical",
                    "instance": "10.10.0.13:9100",
                    "node": "db-01",
                    "category": "disk",
                },
                "annotations": {"summary": "Disk almost full on db-01", "description": "94% used"},
                "startsAt": "2026-01-01T00:00:00Z",
            }
        ],
    }
    response = client.post(f"{API}/webhooks/alertmanager", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["received"] == 1
    assert body["incidents_opened"] == 1
    assert body["recoveries_triggered"] == 1

    # duplicate delivery must not create a second incident
    again = client.post(f"{API}/webhooks/alertmanager", json=payload)
    assert again.json()["incidents_opened"] == 0
    assert client.get(f"{API}/incidents").json()["total"] == 1

    alerts = client.get(f"{API}/alerts").json()
    assert len(alerts) == 2  # one row per delivery, audit trail
    assert alerts[0]["alertname"] == "HostDiskFull"


def test_alert_resolution_closes_incident(client):
    firing = {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": "HighMemoryUsage", "severity": "warning", "instance": "10.10.0.12:9100"},
                "annotations": {"summary": "High memory on api-01"},
            }
        ],
    }
    client.post(f"{API}/webhooks/alertmanager", json=firing)
    assert client.get(f"{API}/incidents").json()["total"] == 1

    resolved = {
        "status": "resolved",
        "alerts": [
            {
                "status": "resolved",
                "labels": {"alertname": "HighMemoryUsage", "severity": "warning", "instance": "10.10.0.12:9100"},
                "annotations": {"summary": "High memory on api-01"},
            }
        ],
    }
    response = client.post(f"{API}/webhooks/alertmanager", json=resolved)
    assert response.json()["incidents_resolved"] == 1
    incidents = client.get(f"{API}/incidents").json()["items"]
    assert incidents[0]["status"] == "resolved"


def test_overview_aggregates_dashboard_data(client):
    client.post(
        f"{API}/incidents",
        json={"title": "Test", "hostname": "web-01", "category": "memory", "severity": "critical"},
    )
    response = client.get(f"{API}/overview")
    assert response.status_code == 200
    body = response.json()
    assert body["hosts"]["unknown"] + body["hosts"]["up"] + body["hosts"]["down"] == 3
    assert body["incidents"]["open"] == 1
    assert set(body["kpis"]) == {"availability", "mttr", "recovery_success", "open_incidents"}
    assert len(body["host_status"]) == 3


def test_health_report_generation(client):
    response = client.post(f"{API}/reports?hours=24")
    assert response.status_code == 201
    report = response.json()
    assert report["hosts_total"] == 3
    assert "per_host" in report["payload"]
    listed = client.get(f"{API}/reports").json()
    assert len(listed) == 1


def test_events_feed_records_actions(client):
    client.post(
        f"{API}/incidents",
        json={"title": "Manual event test", "hostname": "api-01", "category": "cpu", "severity": "warning"},
    )
    events = client.get(f"{API}/events").json()
    assert any(event["category"] == "incident" for event in events)


def test_self_metrics_exposition(client):
    response = client.get("/metrics")
    assert response.status_code == 200
    text = response.text
    assert "ship_hosts_total" in text
    assert "ship_recovery_actions_total" in text
    assert "# TYPE ship_mttr_seconds gauge" in text


def test_simulation_reports_unreachable_simulator(client):
    response = client.get(f"{API}/simulation")
    assert response.status_code == 200
    body = response.json()
    assert body["simulator_reachable"] is False
    assert "unreachable" in body["detail"]


def test_simulation_injection_returns_503_when_simulator_down(client):
    response = client.post(f"{API}/simulation/faults", json={"hostname": "web-01", "metric": "cpu", "value": 99})
    assert response.status_code == 503


def test_detection_endpoint_reports_prometheus_failure(client):
    response = client.post(f"{API}/detections/run")
    assert response.status_code == 200
    body = response.json()
    assert body["hosts_evaluated"] == 0
    assert body["errors"]


def test_incident_not_found(client):
    assert client.get(f"{API}/incidents/424242").status_code == 404
    assert client.post(f"{API}/incidents/424242/recover", json={}).status_code == 404

def test_resolved_alert_clears_firing_state(client):
    labels = {"alertname": "MemoryPressure", "severity": "critical", "instance": "10.10.0.12:9100", "metric": "memory"}

    client.post(f"{API}/webhooks/alertmanager", json={"status": "firing", "alerts": [{"status": "firing", "labels": labels}]})
    assert client.get(f"{API}/alerts/summary").json()["firing"] == 1

    client.post(f"{API}/webhooks/alertmanager", json={"status": "resolved", "alerts": [{"status": "resolved", "labels": labels}]})

    summary = client.get(f"{API}/alerts/summary").json()
    assert summary["firing"] == 0
    assert summary["resolved"] == 2
    incidents = client.get(f"{API}/incidents").json()["items"]
    assert len(incidents) == 1
    assert incidents[0]["status"] == "resolved"
