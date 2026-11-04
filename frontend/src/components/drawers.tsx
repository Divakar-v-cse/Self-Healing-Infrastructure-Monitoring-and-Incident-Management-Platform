import { useEffect, useState } from 'react';
import { api, type Host, type IncidentDetail, type MetricSeries } from '../lib/api';
import { formatDateTime, formatDuration, humanMetric, timeAgo, titleCase } from '../lib/format';
import { Chart, IncidentStatusBadge, RecoveryBadge, SeverityBadge, StatusBadge, Switch } from './ui';

function CloseButton({ onClick }: { onClick: () => void }) {
  return (
    <button className="btn ghost sm" onClick={onClick} aria-label="Close panel">
      ✕
    </button>
  );
}

export function IncidentDrawer({
  incidentId,
  onClose,
  onChanged,
  notify,
}: {
  incidentId: number;
  onClose: () => void;
  onChanged: () => void;
  notify: (message: string, tone?: 'ok' | 'error') => void;
}) {
  const [detail, setDetail] = useState<IncidentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = async () => {
    try {
      setDetail(await api.incident(incidentId));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load incident');
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [incidentId]);

  const act = async (key: string, action: () => Promise<string>) => {
    setBusy(key);
    try {
      const message = await action();
      notify(message, 'ok');
      await load();
      onChanged();
    } catch (err) {
      notify(err instanceof Error ? err.message : 'Action failed', 'error');
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" onClick={(event) => event.stopPropagation()}>
        <div className="row">
          <span className="ref">{detail?.reference ?? `#${incidentId}`}</span>
          <span className="spacer grow" />
          <CloseButton onClick={onClose} />
        </div>

        {error && <div className="banner error">{error}</div>}
        {!detail && !error && <div className="empty">Loading incident…</div>}

        {detail && (
          <>
            <h2>{detail.title}</h2>
            <div className="row small dim" style={{ marginBottom: 10 }}>
              <SeverityBadge severity={detail.severity} />
              <IncidentStatusBadge status={detail.status} />
              <span className="badge muted">{detail.category}</span>
              {detail.hostname && <span className="mono">{detail.hostname}</span>}
            </div>

            <div className="toolbar">
              <button
                className="btn primary sm"
                disabled={busy !== null || detail.status === 'resolved'}
                onClick={() =>
                  act('recover', async () => {
                    const result = await api.recover(detail.id, { reason: 'operator initiated from dashboard' });
                    return result.allowed
                      ? `Recovery dispatched: ${result.status}`
                      : `Blocked by safeguard (${result.rule}): ${result.reason}`;
                  })
                }
              >
                {busy === 'recover' ? 'Dispatching…' : 'Run recovery'}
              </button>
              <button
                className="btn sm"
                disabled={busy !== null || detail.status !== 'open'}
                onClick={() =>
                  act('ack', async () => {
                    await api.updateIncident(detail.id, { status: 'acknowledged' });
                    return 'Incident acknowledged';
                  })
                }
              >
                Acknowledge
              </button>
              <button
                className="btn sm"
                disabled={busy !== null || detail.status === 'resolved'}
                onClick={() =>
                  act('resolve', async () => {
                    await api.updateIncident(detail.id, { status: 'resolved' });
                    return 'Incident resolved';
                  })
                }
              >
                Resolve
              </button>
            </div>

            <div className="drawer-section">
              <h3>Signal</h3>
              <dl className="kv">
                <dt>Detected</dt>
                <dd>{formatDateTime(detail.detected_at)} ({timeAgo(detail.detected_at)})</dd>
                <dt>Resolved</dt>
                <dd>{detail.resolved_at ? formatDateTime(detail.resolved_at) : '—'}</dd>
                <dt>Time to recovery</dt>
                <dd>{formatDuration(detail.mttr_seconds)}</dd>
                <dt>Metric</dt>
                <dd className="mono">{detail.metric ?? '—'}</dd>
                <dt>Observed</dt>
                <dd className="mono">
                  {detail.metric ? humanMetric(detail.metric, detail.observed_value) : detail.observed_value ?? '—'}
                </dd>
                <dt>Threshold</dt>
                <dd className="mono">
                  {detail.metric ? humanMetric(detail.metric, detail.threshold_value) : detail.threshold_value ?? '—'}
                </dd>
                <dt>Playbook</dt>
                <dd className="mono">{detail.recommended_playbook ?? '—'}</dd>
                <dt>Fingerprint</dt>
                <dd className="mono">{detail.fingerprint}</dd>
              </dl>
            </div>

            <div className="drawer-section">
              <h3>Classification</h3>
              <p className="small muted" style={{ margin: 0 }}>{detail.classification_reason ?? '—'}</p>
              {detail.root_cause && (
                <p className="small" style={{ marginTop: 8 }}>
                  <strong>Hypothesis:</strong> {detail.root_cause}
                </p>
              )}
              {Array.isArray(detail.details?.runbook) && (
                <ol className="small muted" style={{ paddingLeft: 18, marginTop: 8 }}>
                  {(detail.details.runbook as string[]).map((step) => (
                    <li key={step}>{step}</li>
                  ))}
                </ol>
              )}
            </div>

            <div className="drawer-section">
              <h3>Recovery history ({detail.recovery_actions.length})</h3>
              {detail.recovery_actions.length === 0 && <div className="empty">No recovery attempted yet.</div>}
              <div className="timeline">
                {detail.recovery_actions.map((action) => (
                  <div
                    key={action.id}
                    className={`timeline-item ${
                      action.status === 'success' || action.status === 'simulated'
                        ? 'ok'
                        : action.status === 'blocked'
                          ? 'warn'
                          : action.status === 'running'
                            ? 'info'
                            : 'crit'
                    }`}
                  >
                    <div className="timeline-head">
                      <span>Attempt #{action.attempt}</span>
                      <RecoveryBadge status={action.status} />
                      {action.dry_run && <span className="badge muted">dry-run</span>}
                      <span className="mono">{action.playbook}</span>
                    </div>
                    <div className="timeline-meta">
                      {formatDateTime(action.created_at)} · by {action.triggered_by}
                      {action.duration_seconds !== null && ` · ${action.duration_seconds.toFixed(2)}s`}
                    </div>
                    {action.safeguard_reason && (
                      <div className="small" style={{ color: 'var(--warn)', marginTop: 4 }}>
                        {action.safeguard_reason}
                      </div>
                    )}
                    {action.output && <pre className="output" style={{ marginTop: 6 }}>{action.output}</pre>}
                  </div>
                ))}
              </div>
            </div>
          </>
        )}
      </aside>
    </div>
  );
}

export function HostDrawer({
  host,
  onClose,
  notify,
}: {
  host: Host;
  onClose: () => void;
  notify: (message: string, tone?: 'ok' | 'error') => void;
}) {
  const [series, setSeries] = useState<MetricSeries[]>([]);
  const [maintenance, setMaintenance] = useState(host.maintenance_mode);
  const [autoRecovery, setAutoRecovery] = useState(host.auto_recovery_enabled);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    api
      .hostMetrics(host.id, 120)
      .then((data) => {
        if (!cancelled) setSeries(data);
      })
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [host.id]);

  const update = async (body: { maintenance_mode?: boolean; auto_recovery_enabled?: boolean }) => {
    try {
      await api.updateHost(host.id, body);
      notify(`Host ${host.hostname} updated`, 'ok');
    } catch (err) {
      notify(err instanceof Error ? err.message : 'Update failed', 'error');
    }
  };

  const points = (metric: string) => series.find((item) => item.metric === metric)?.points.map((p) => p.value) ?? [];
  const colors: Record<string, string> = {
    cpu: '#38bdf8',
    memory: '#a78bfa',
    disk: '#fbbf24',
    network: '#34d399',
  };

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" onClick={(event) => event.stopPropagation()}>
        <div className="row">
          <span className="ref">{host.hostname}</span>
          <StatusBadge status={host.status} />
          <span className="spacer grow" />
          <CloseButton onClick={onClose} />
        </div>

        <h2 style={{ marginTop: 10 }}>{host.hostname}</h2>
        <div className="small dim mono">
          {host.instance ?? 'no instance'} · {host.role} · {host.environment}
        </div>

        <div className="drawer-section">
          <h3>Last 2 hours</h3>
          {loading && <div className="empty">Loading metrics…</div>}
          {!loading && (
            <div className="stack" style={{ gap: 10 }}>
              {['cpu', 'memory', 'disk', 'network'].map((metric) => (
                <div key={metric}>
                  <div className="small dim" style={{ marginBottom: 2 }}>{titleCase(metric)}</div>
                  <Chart series={[{ label: metric, color: colors[metric], values: points(metric) }]} height={96} />
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="drawer-section">
          <h3>Operations</h3>
          <div className="stack" style={{ gap: 10 }}>
            <Switch
              checked={maintenance}
              label="Maintenance mode (suppresses automated recovery)"
              onChange={(next) => {
                setMaintenance(next);
                void update({ maintenance_mode: next });
              }}
            />
            <Switch
              checked={autoRecovery}
              label="Allow automated recovery on this host"
              onChange={(next) => {
                setAutoRecovery(next);
                void update({ auto_recovery_enabled: next });
              }}
            />
          </div>
        </div>

        <div className="drawer-section">
          <h3>Inventory</h3>
          <dl className="kv">
            <dt>IP address</dt>
            <dd className="mono">{host.ip_address ?? '—'}</dd>
            <dt>Operating system</dt>
            <dd>{host.os ?? '—'}</dd>
            <dt>Last seen</dt>
            <dd>{timeAgo(host.last_seen)}</dd>
            <dt>Open incidents</dt>
            <dd>{host.open_incidents}</dd>
            <dt>Labels</dt>
            <dd className="mono">{JSON.stringify(host.labels)}</dd>
          </dl>
        </div>
      </aside>
    </div>
  );
}
