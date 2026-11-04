import { api, type Host } from '../lib/api';
import { formatPercent, humanMetric, timeAgo } from '../lib/format';
import { usePolling } from '../lib/usePolling';
import { Card, EmptyState, IncidentStatusBadge, Meter, SeverityBadge, StatCard, StatusBadge } from '../components/ui';

function HostTile({ host, onOpen }: { host: Host; onOpen: (host: Host) => void }) {
  const metrics = host.latest_metrics ?? {};
  return (
    <button
      type="button"
      className={`host-card ${host.status}`}
      onClick={() => onOpen(host)}
      style={{ textAlign: 'left', cursor: 'pointer' }}
    >
      <div className="host-head">
        <span className="host-name">{host.hostname}</span>
        <span className="spacer grow" />
        <StatusBadge status={host.status} />
      </div>
      <div className="host-meta">
        {host.instance ?? '—'} · {host.role}
        {host.maintenance_mode && <span className="badge muted" style={{ marginLeft: 6 }}>maintenance</span>}
      </div>

      <div className="host-metrics">
        {(['cpu', 'memory', 'disk'] as const).map((metric) => (
          <div className="metric-row" key={metric}>
            <span>{metric === 'cpu' ? 'CPU' : metric === 'memory' ? 'MEM' : 'DISK'}</span>
            <Meter value={metrics[metric] ?? 0} />
            <span className="value">{formatPercent(metrics[metric])}</span>
          </div>
        ))}
        <div className="metric-row">
          <span>NET</span>
          <span className="dim mono" style={{ fontSize: 10.5 }}>
            {metrics.network ? humanMetric('network', metrics.network) : '—'}
          </span>
          <span className="value">{host.open_incidents > 0 ? `${host.open_incidents} inc` : 'ok'}</span>
        </div>
      </div>

      <div className="host-footer small dim">seen {timeAgo(host.last_seen)}</div>
    </button>
  );
}

export default function Overview({
  onOpenIncident,
  onOpenHost,
}: {
  onOpenIncident: (id: number) => void;
  onOpenHost: (host: Host) => void;
}) {
  const overview = usePolling(() => api.overview(), 15000);
  const incidents = usePolling(() => api.incidents({ status: 'active', page_size: 8 }), 15000);
  const alerts = usePolling(() => api.alerts(10), 20000);
  const events = usePolling(() => api.events(12), 20000);

  const data = overview.data;
  const kpis = data?.kpis;

  return (
    <div className="stack">
      {overview.error && <div className="banner error">Unable to load overview: {overview.error}</div>}

      <div className="kpi-grid">
        <StatCard
          label="Fleet availability"
          value={kpis ? kpis.availability.value.toFixed(1) : '—'}
          unit="%"
          hint={`${data?.hosts.up ?? 0} up · ${data?.hosts.degraded ?? 0} degraded · ${data?.hosts.down ?? 0} down`}
          tone={(data?.hosts.down ?? 0) > 0 ? 'crit' : 'ok'}
        />
        <StatCard
          label="Open incidents"
          value={data?.incidents.open ?? 0}
          hint={`${data?.incidents.critical ?? 0} critical · ${data?.incidents.resolved_in_window ?? 0} resolved in 24h`}
          tone={(data?.incidents.critical ?? 0) > 0 ? 'crit' : data?.incidents.open ? 'warn' : 'ok'}
        />
        <StatCard
          label="Mean time to recovery"
          value={kpis ? kpis.mttr.value.toFixed(1) : '—'}
          unit="min"
          hint="across resolved incidents"
        />
        <StatCard
          label="Recovery success"
          value={kpis ? kpis.recovery_success.value.toFixed(0) : '—'}
          unit="%"
          hint={`${data?.recoveries.successful ?? 0} ok · ${data?.recoveries.blocked ?? 0} blocked by safeguards`}
          tone={(kpis?.recovery_success.value ?? 0) >= 85 ? 'ok' : 'warn'}
        />
      </div>

      <Card
        title={`Server status (${data?.host_status.length ?? 0})`}
        action={<span className="small dim">refreshed {timeAgo(data?.generated_at)}</span>}
      >
        {!data && <EmptyState>Loading hosts…</EmptyState>}
        {data && data.host_status.length === 0 && (
          <EmptyState>No hosts discovered yet. Prometheus scrapes populate this list automatically.</EmptyState>
        )}
        <div className="host-grid">
          {data?.host_status.map((host) => (
            <HostTile key={host.id} host={host} onOpen={onOpenHost} />
          ))}
        </div>
      </Card>

      <div className="grid-2">
        <Card
          title="Active incidents"
          action={
            <span className="small dim">
              {(data?.incidents.in_window ?? 0)} in last 24h · {(data?.alerts.critical ?? 0)} critical alerts
            </span>
          }
        >
          {incidents.data?.items.length === 0 && <EmptyState>No active incidents — the fleet is healthy.</EmptyState>}
          <div>
            {incidents.data?.items.map((incident) => (
              <div
                key={incident.id}
                className="incident-row"
                role="button"
                tabIndex={0}
                onClick={() => onOpenIncident(incident.id)}
                onKeyDown={(event) => event.key === 'Enter' && onOpenIncident(incident.id)}
              >
                <div className="incident-main">
                  <div className="incident-title">
                    <span className="ref">{incident.reference}</span>
                    {incident.title}
                  </div>
                  <div className="incident-sub">
                    <span>{incident.hostname ?? 'unassigned'}</span>
                    <span className="badge muted">{incident.category}</span>
                    {incident.metric && (
                      <span className="mono">
                        {incident.metric}={humanMetric(incident.metric, incident.observed_value)}
                      </span>
                    )}
                    <span>{timeAgo(incident.detected_at)}</span>
                  </div>
                </div>
                <div className="row" style={{ alignSelf: 'center', gap: 6 }}>
                  <SeverityBadge severity={incident.severity} />
                  <IncidentStatusBadge status={incident.status} />
                </div>
              </div>
            ))}
          </div>
        </Card>

        <div className="stack">
          <Card title="Alert stream">
            {alerts.data?.length === 0 && <EmptyState>No alerts received from Alertmanager.</EmptyState>}
            <div className="feed" style={{ maxHeight: 240 }}>
              {alerts.data?.map((alert) => (
                <div key={alert.id} className={`feed-item ${alert.status === 'firing' ? 'warning' : 'info'}`}>
                  <span className="icon">{alert.status === 'firing' ? '!' : '✓'}</span>
                  <div className="feed-body">
                    <div className="feed-title">{alert.summary ?? alert.alertname}</div>
                    <div className="feed-meta">
                      <span className="mono">{alert.alertname}</span>
                      <span>{alert.instance ?? '—'}</span>
                      <span>{timeAgo(alert.received_at)}</span>
                      <SeverityBadge severity={alert.severity} />
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </Card>

          <Card title="Recovery activity">
            {events.data?.length === 0 && <EmptyState>No platform events yet.</EmptyState>}
            <div className="feed" style={{ maxHeight: 260 }}>
              {events.data?.map((event) => (
                <div key={event.id} className={`feed-item ${event.level}`}>
                  <span className="icon">
                    {event.category === 'recovery' ? '↻' : event.category === 'incident' ? '▲' : '·'}
                  </span>
                  <div className="feed-body">
                    <div className="feed-title">{event.message}</div>
                    <div className="feed-meta">
                      <span>{event.actor}</span>
                      <span>{event.category}</span>
                      <span>{timeAgo(event.created_at)}</span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
