import { useState } from 'react';
import { api, type Host } from '../lib/api';
import { formatPercent, humanMetric, timeAgo } from '../lib/format';
import { usePolling } from '../lib/usePolling';
import { Card, EmptyState, Meter, StatusBadge } from '../components/ui';

export default function Hosts({ onOpenHost }: { onOpenHost: (host: Host) => void }) {
  const hosts = usePolling(() => api.hosts(), 15000);
  const [filter, setFilter] = useState('');

  const list = (hosts.data ?? []).filter((host) => (filter ? host.status === filter : true));

  return (
    <div className="stack">
      <Card
        title="Host inventory"
        action={
          <div className="row">
            {['', 'up', 'degraded', 'down', 'unknown'].map((value) => (
              <button
                key={value || 'all'}
                className={`chip ${filter === value ? 'active' : ''}`}
                onClick={() => setFilter(value)}
              >
                {value || 'all'}
              </button>
            ))}
          </div>
        }
      >
        {hosts.error && <div className="banner error">{hosts.error}</div>}
        {!hosts.data && <EmptyState>Loading hosts…</EmptyState>}
        {hosts.data && list.length === 0 && <EmptyState>No hosts in this state.</EmptyState>}

        {list.length > 0 && (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Host</th>
                  <th>Status</th>
                  <th>Role</th>
                  <th>Instance</th>
                  <th>CPU</th>
                  <th>Memory</th>
                  <th>Disk</th>
                  <th>Network</th>
                  <th>Incidents</th>
                  <th>Last seen</th>
                </tr>
              </thead>
              <tbody>
                {list.map((host) => (
                  <tr key={host.id} className="clickable" onClick={() => onOpenHost(host)}>
                    <td>
                      <div style={{ fontWeight: 600 }}>{host.hostname}</div>
                      <div className="small dim">
                        {host.maintenance_mode ? 'maintenance mode' : host.environment}
                      </div>
                    </td>
                    <td><StatusBadge status={host.status} /></td>
                    <td>{host.role}</td>
                    <td className="mono">{host.instance ?? '—'}</td>
                    <td style={{ minWidth: 90 }}>
                      <Meter value={host.latest_metrics.cpu ?? 0} />
                      <span className="mono small">{formatPercent(host.latest_metrics.cpu)}</span>
                    </td>
                    <td style={{ minWidth: 90 }}>
                      <Meter value={host.latest_metrics.memory ?? 0} />
                      <span className="mono small">{formatPercent(host.latest_metrics.memory)}</span>
                    </td>
                    <td style={{ minWidth: 90 }}>
                      <Meter value={host.latest_metrics.disk ?? 0} />
                      <span className="mono small">{formatPercent(host.latest_metrics.disk)}</span>
                    </td>
                    <td className="mono small">{humanMetric('network', host.latest_metrics.network)}</td>
                    <td>
                      {host.open_incidents > 0 ? (
                        <span className="badge crit">{host.open_incidents} open</span>
                      ) : (
                        <span className="badge ok">clear</span>
                      )}
                    </td>
                    <td className="small dim nowrap">{timeAgo(host.last_seen)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="How hosts are discovered">
        <p className="small muted" style={{ margin: 0 }}>
          Every <span className="mono">job="node"</span> target scraped by Prometheus becomes a host record. The
          detector refreshes status, persists metric samples and opens incidents when a threshold is breached. Hosts
          can also be registered manually through <span className="mono">POST /api/v1/hosts</span>.
        </p>
      </Card>
    </div>
  );
}
