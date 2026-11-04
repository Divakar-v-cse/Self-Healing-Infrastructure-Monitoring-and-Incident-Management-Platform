import { useState } from 'react';
import { api } from '../lib/api';
import { formatDateTime, formatDuration, timeAgo, titleCase } from '../lib/format';
import { usePolling } from '../lib/usePolling';
import { Card, EmptyState, SeverityBadge, StatCard } from '../components/ui';

export default function Reports({ notify }: { notify: (message: string, tone?: 'ok' | 'error') => void }) {
  const reports = usePolling(() => api.reports(), 30000);
  const [selected, setSelected] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  const current = reports.data?.find((report) => report.id === selected) ?? reports.data?.[0] ?? null;

  const generate = async () => {
    setBusy(true);
    try {
      const report = await api.createReport(24);
      notify(`Health report #${report.id} generated`, 'ok');
      await reports.reload();
      setSelected(report.id);
    } catch (err) {
      notify(err instanceof Error ? err.message : 'Could not generate report', 'error');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      <Card
        title="Health reports"
        action={
          <button className="btn primary sm" onClick={() => void generate()} disabled={busy}>
            {busy ? 'Generating…' : 'Generate report (24h)'}
          </button>
        }
      >
        {reports.error && <div className="banner error">{reports.error}</div>}
        {reports.data?.length === 0 && (
          <EmptyState>No reports yet. Generate one to snapshot fleet health, incidents and recovery outcomes.</EmptyState>
        )}
        {reports.data && reports.data.length > 0 && (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Generated</th>
                  <th>Window</th>
                  <th>Availability</th>
                  <th>Incidents</th>
                  <th>Critical</th>
                  <th>Recovery rate</th>
                  <th>MTTR</th>
                </tr>
              </thead>
              <tbody>
                {reports.data.map((report) => (
                  <tr
                    key={report.id}
                    className="clickable"
                    onClick={() => setSelected(report.id)}
                    style={current?.id === report.id ? { background: 'rgba(56,189,248,0.06)' } : undefined}
                  >
                    <td className="mono">{report.id}</td>
                    <td className="small">{timeAgo(report.generated_at)}</td>
                    <td className="small dim">
                      {formatDateTime(report.window_start)} → {formatDateTime(report.window_end)}
                    </td>
                    <td className="mono">{report.availability_percent.toFixed(1)}%</td>
                    <td className="mono">{report.incidents_total}</td>
                    <td className="mono">{report.incidents_critical}</td>
                    <td className="mono">{report.recovery_success_rate.toFixed(0)}%</td>
                    <td className="mono">{formatDuration(report.mean_time_to_recovery_seconds)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {current && (
        <>
          <div className="kpi-grid">
            <StatCard
              label="Availability"
              value={current.availability_percent.toFixed(1)}
              unit="%"
              hint={`${current.hosts_up}/${current.hosts_total} hosts healthy`}
              tone={current.hosts_down > 0 ? 'crit' : 'ok'}
            />
            <StatCard
              label="Incidents (window)"
              value={current.incidents_total}
              hint={`${current.incidents_critical} critical · ${current.incidents_open} still open`}
              tone={current.incidents_critical > 0 ? 'warn' : 'default'}
            />
            <StatCard
              label="Recovery success"
              value={current.recovery_success_rate.toFixed(0)}
              unit="%"
              hint={`${current.recoveries_success}/${current.recoveries_total} actions`}
            />
            <StatCard
              label="Mean time to recovery"
              value={(current.mean_time_to_recovery_seconds ?? 0).toFixed(0)}
              unit="s"
            />
          </div>

          <div className="grid-2">
            <Card title="Resource averages">
              <dl className="kv">
                <dt>CPU</dt>
                <dd className="mono">{current.avg_cpu_percent.toFixed(2)}%</dd>
                <dt>Memory</dt>
                <dd className="mono">{current.avg_memory_percent.toFixed(2)}%</dd>
                <dt>Disk</dt>
                <dd className="mono">{current.avg_disk_percent.toFixed(2)}%</dd>
                <dt>Report window</dt>
                <dd className="small">{formatDateTime(current.window_start)} → {formatDateTime(current.window_end)}</dd>
              </dl>

              <h3 style={{ marginTop: 16, fontSize: 11.5, textTransform: 'uppercase', color: 'var(--text-dim)' }}>
                Incidents by category
              </h3>
              {Object.keys(current.payload.incidents_by_category ?? {}).length === 0 ? (
                <p className="small dim">No incidents in this window.</p>
              ) : (
                <div className="row" style={{ gap: 8 }}>
                  {Object.entries(current.payload.incidents_by_category ?? {}).map(([category, count]) => (
                    <span key={category} className="badge info">
                      {titleCase(category)}: {count}
                    </span>
                  ))}
                </div>
              )}
            </Card>

            <Card title="Host breakdown">
              <div className="table-wrap">
                <table className="data" style={{ minWidth: 380 }}>
                  <thead>
                    <tr>
                      <th>Host</th>
                      <th>Status</th>
                      <th>Incidents</th>
                      <th>Critical</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(current.payload.per_host ?? []).map((host) => (
                      <tr key={host.hostname}>
                        <td>
                          {host.hostname}
                          {host.maintenance_mode && <span className="badge muted" style={{ marginLeft: 6 }}>maint</span>}
                        </td>
                        <td className="small">{host.status}</td>
                        <td className="mono">{host.incidents}</td>
                        <td className="mono">{host.critical_incidents}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>

          <Card title="Top incidents">
            {(current.payload.top_incidents ?? []).length === 0 && <EmptyState>No incidents in this window.</EmptyState>}
            {(current.payload.top_incidents ?? []).map((incident) => (
              <div key={incident.reference} className="incident-row" style={{ cursor: 'default' }}>
                <div className="incident-main">
                  <div className="incident-title">
                    <span className="ref">{incident.reference}</span>
                    {incident.title}
                  </div>
                  <div className="incident-sub">
                    <span>{incident.status}</span>
                    <span>{formatDateTime(incident.detected_at)}</span>
                    <span>TTR {formatDuration(incident.mttr_seconds)}</span>
                  </div>
                </div>
                <SeverityBadge severity={incident.severity} />
              </div>
            ))}
          </Card>
        </>
      )}
    </div>
  );
}
