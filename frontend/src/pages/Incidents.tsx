import { useMemo, useState } from 'react';
import { api, type Incident } from '../lib/api';
import { formatDuration, humanMetric, timeAgo } from '../lib/format';
import { usePolling } from '../lib/usePolling';
import {
  Card,
  EmptyState,
  IncidentStatusBadge,
  SeverityBadge,
} from '../components/ui';

const STATUS_FILTERS = [
  { key: 'active', label: 'Active' },
  { key: 'open', label: 'Open' },
  { key: 'acknowledged', label: 'Acknowledged' },
  { key: 'recovering', label: 'Recovering' },
  { key: 'resolved', label: 'Resolved' },
  { key: '', label: 'All' },
];

export default function Incidents({
  notify,
  onOpenIncident,
  onChanged,
}: {
  notify: (message: string, tone?: 'ok' | 'error') => void;
  onOpenIncident: (id: number) => void;
  onChanged: () => void;
}) {
  const [status, setStatus] = useState('active');
  const [severity, setSeverity] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const pageSize = 15;

  const query = useMemo(
    () => ({ status, severity, search, page, page_size: pageSize }),
    [status, severity, search, page],
  );
  const incidents = usePolling(() => api.incidents(query), 15000);

  const act = async (incident: Incident, action: 'acknowledged' | 'resolved') => {
    try {
      await api.updateIncident(incident.id, { status: action });
      notify(`${incident.reference} ${action}`, 'ok');
      incidents.refresh();
      onChanged();
    } catch (err) {
      notify(err instanceof Error ? err.message : 'Update failed', 'error');
    }
  };

  const recover = async (incident: Incident) => {
    try {
      const result = await api.recover(incident.id, { reason: 'operator initiated from incident list' });
      notify(
        result.allowed
          ? `${incident.reference}: recovery ${result.status}`
          : `${incident.reference} blocked (${result.rule}): ${result.reason}`,
        result.allowed ? 'ok' : 'error',
      );
      incidents.refresh();
      onChanged();
    } catch (err) {
      notify(err instanceof Error ? err.message : 'Recovery failed', 'error');
    }
  };

  const total = incidents.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="stack">
      <Card title="Incident queue" action={<span className="small dim">{total} matching</span>}>
        <div className="filters">
          {STATUS_FILTERS.map((filter) => (
            <button
              key={filter.key || 'all'}
              className={`chip ${status === filter.key ? 'active' : ''}`}
              onClick={() => {
                setStatus(filter.key);
                setPage(1);
              }}
            >
              {filter.label}
            </button>
          ))}
          <span className="spacer grow" />
          <select
            className="input"
            style={{ width: 130 }}
            value={severity}
            onChange={(event) => {
              setSeverity(event.target.value);
              setPage(1);
            }}
          >
            <option value="">All severities</option>
            <option value="critical">Critical</option>
            <option value="warning">Warning</option>
            <option value="info">Info</option>
          </select>
          <input
            className="input"
            style={{ width: 220 }}
            placeholder="Search reference or title…"
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setPage(1);
            }}
          />
        </div>

        {incidents.error && <div className="banner error">{incidents.error}</div>}
        {incidents.data?.items.length === 0 && <EmptyState>No incidents match these filters.</EmptyState>}

        {incidents.data && incidents.data.items.length > 0 && (
          <>
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Reference</th>
                    <th>Title</th>
                    <th>Host</th>
                    <th>Signal</th>
                    <th>Severity</th>
                    <th>Status</th>
                    <th>Detected</th>
                    <th>TTR</th>
                    <th className="right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {incidents.data.items.map((incident) => (
                    <tr key={incident.id} className="clickable" onClick={() => onOpenIncident(incident.id)}>
                      <td className="ref">{incident.reference}</td>
                      <td>{incident.title}</td>
                      <td className="mono">{incident.hostname ?? '—'}</td>
                      <td className="mono">
                        {incident.metric ? `${incident.metric}=${humanMetric(incident.metric, incident.observed_value)}` : '—'}
                      </td>
                      <td><SeverityBadge severity={incident.severity} /></td>
                      <td><IncidentStatusBadge status={incident.status} /></td>
                      <td className="small dim nowrap">{timeAgo(incident.detected_at)}</td>
                      <td className="mono">{formatDuration(incident.mttr_seconds)}</td>
                      <td onClick={(event) => event.stopPropagation()}>
                        <div className="row" style={{ justifyContent: 'flex-end', gap: 5 }}>
                          {incident.status !== 'resolved' && (
                            <button className="btn sm" onClick={() => void recover(incident)}>Recover</button>
                          )}
                          {incident.status === 'open' && (
                            <button className="btn ghost sm" onClick={() => void act(incident, 'acknowledged')}>Ack</button>
                          )}
                          {incident.status !== 'resolved' && (
                            <button className="btn ghost sm" onClick={() => void act(incident, 'resolved')}>Resolve</button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="pagination">
              <button className="btn sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Prev</button>
              <span>page {page} / {pages}</span>
              <button className="btn sm" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>Next</button>
            </div>
          </>
        )}
      </Card>
    </div>
  );
}
