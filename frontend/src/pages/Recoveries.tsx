import { useMemo, useState } from 'react';
import { api, type RecoveryAction } from '../lib/api';
import { formatDateTime, formatDuration, timeAgo } from '../lib/format';
import { usePolling } from '../lib/usePolling';
import { Card, EmptyState, RecoveryBadge, StatCard } from '../components/ui';

const FILTERS = ['', 'running', 'simulated', 'success', 'failed', 'blocked'] as const;

export default function Recoveries({ onOpenIncident }: { onOpenIncident: (id: number) => void }) {
  const recoveries = usePolling(() => api.recoveries(200), 12000);
  const [filter, setFilter] = useState<string>('');
  const [expanded, setExpanded] = useState<number | null>(null);

  const stats = useMemo(() => {
    const actions = recoveries.data ?? [];
    const executed = actions.filter((action) =>
      ['success', 'simulated', 'failed', 'timeout'].includes(action.status),
    );
    const successful = actions.filter((action) => ['success', 'simulated'].includes(action.status));
    return {
      total: actions.length,
      executed: executed.length,
      successRate: executed.length ? (successful.length / executed.length) * 100 : 0,
      blocked: actions.filter((action) => action.status === 'blocked').length,
      dryRun: actions.filter((action) => action.dry_run).length,
    };
  }, [recoveries.data]);

  const list = (recoveries.data ?? []).filter((action) => (filter ? action.status === filter : true));

  const renderOutput = (action: RecoveryAction) => {
    if (expanded !== action.id) return null;
    if (!action.output && !action.command) return <div className="small dim">No output recorded.</div>;
    return (
      <div style={{ marginTop: 8 }}>
        {action.command && (
          <div className="small dim" style={{ marginBottom: 4 }}>
            command: <span className="mono">{action.command}</span>
          </div>
        )}
        {action.output && <pre className="output">{action.output}</pre>}
      </div>
    );
  };

  return (
    <div className="stack">
      <div className="kpi-grid">
        <StatCard label="Recovery actions" value={stats.total} hint={`${stats.executed} executed`} />
        <StatCard
          label="Success rate"
          value={stats.successRate.toFixed(0)}
          unit="%"
          tone={stats.successRate >= 85 ? 'ok' : 'warn'}
          hint="successful or simulated / executed"
        />
        <StatCard
          label="Blocked by safeguards"
          value={stats.blocked}
          tone={stats.blocked > 0 ? 'warn' : 'default'}
          hint="loop protection engaged"
        />
        <StatCard label="Dry-run actions" value={stats.dryRun} hint="Ansible not actually executed" />
      </div>

      <Card
        title="Recovery log"
        action={
          <div className="row">
            {FILTERS.map((value) => (
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
        {recoveries.error && <div className="banner error">{recoveries.error}</div>}
        {list.length === 0 && <EmptyState>No recovery actions recorded yet.</EmptyState>}

        {list.length > 0 && (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Incident</th>
                  <th>Playbook</th>
                  <th>Attempt</th>
                  <th>Status</th>
                  <th>Triggered by</th>
                  <th>Duration</th>
                  <th>When</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {list.map((action) => (
                  <tr
                    key={action.id}
                    className="clickable"
                    onClick={() => setExpanded(expanded === action.id ? null : action.id)}
                  >
                    <td className="mono">{action.id}</td>
                    <td>
                      {action.incident_id ? (
                        <button
                          className="btn ghost sm"
                          onClick={(event) => {
                            event.stopPropagation();
                            onOpenIncident(action.incident_id as number);
                          }}
                        >
                          #{action.incident_id}
                        </button>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td className="mono">{action.playbook ?? '—'}</td>
                    <td className="mono">{action.attempt}</td>
                    <td>
                      <RecoveryBadge status={action.status} />
                      {action.safeguard_reason && (
                        <div className="small" style={{ color: 'var(--warn)', marginTop: 3, maxWidth: 280 }}>
                          {action.safeguard_reason}
                        </div>
                      )}
                      {renderOutput(action)}
                    </td>
                    <td className="small">{action.triggered_by}</td>
                    <td className="mono small">{formatDuration(action.duration_seconds)}</td>
                    <td className="small dim nowrap" title={formatDateTime(action.created_at)}>
                      {timeAgo(action.created_at)}
                    </td>
                    <td className="small dim">{expanded === action.id ? '▾' : '▸'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
