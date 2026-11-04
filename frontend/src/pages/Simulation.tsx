import { useState } from 'react';
import { api, type DetectionSummary } from '../lib/api';
import { formatDateTime, formatPercent, timeAgo } from '../lib/format';
import { usePolling } from '../lib/usePolling';
import { Card, EmptyState, Meter } from '../components/ui';

const METRICS = [
  { value: 'cpu', label: 'CPU saturation', hint: 'percent' },
  { value: 'memory', label: 'Memory pressure', hint: 'percent' },
  { value: 'disk', label: 'Disk full', hint: 'percent' },
  { value: 'network', label: 'Network spike', hint: 'Mbit/s' },
  { value: 'down', label: 'Host down', hint: 'makes scraper fail' },
];

export default function Simulation({ notify }: { notify: (message: string, tone?: 'ok' | 'error') => void }) {
  const simulation = usePolling(() => api.simulation(), 8000);
  const [hostname, setHostname] = useState('web-01');
  const [metric, setMetric] = useState('cpu');
  const [value, setValue] = useState('97');
  const [duration, setDuration] = useState('180');
  const [busy, setBusy] = useState<string | null>(null);
  const [summary, setSummary] = useState<DetectionSummary | null>(null);

  const data = simulation.data;
  const reachable = data?.simulator_reachable ?? false;

  const inject = async () => {
    setBusy('inject');
    try {
      await api.injectFault({
        hostname,
        metric,
        value: metric === 'down' ? null : Number(value),
        duration_seconds: Number(duration),
      });
      notify(`Injected ${metric}${metric === 'down' ? '' : `=${value}`} on ${hostname} for ${duration}s`, 'ok');
      await simulation.refresh();
    } catch (err) {
      notify(err instanceof Error ? err.message : 'Injection failed', 'error');
    } finally {
      setBusy(null);
    }
  };

  const clear = async () => {
    setBusy('clear');
    try {
      await api.clearFaults();
      notify('All simulated faults cleared', 'ok');
      await simulation.refresh();
    } catch (err) {
      notify(err instanceof Error ? err.message : 'Could not clear faults', 'error');
    } finally {
      setBusy(null);
    }
  };

  const runDetection = async () => {
    setBusy('detect');
    try {
      const result = await api.runDetection();
      setSummary(result);
      notify(
        result.errors.length
          ? `Detection ran with errors: ${result.errors[0]}`
          : `Detection: ${result.breaches} breaches, ${result.incidents_opened} new incidents`,
        result.errors.length ? 'error' : 'ok',
      );
    } catch (err) {
      notify(err instanceof Error ? err.message : 'Detection failed', 'error');
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="stack">
      {!reachable && (
        <div className="banner error">
          Simulator unreachable at the configured URL.
          {data?.detail ? ` ${data.detail}` : ''} Start it with{' '}
          <span className="mono">python simulator/app.py</span> or <span className="mono">docker compose up simulator</span>.
        </div>
      )}

      <div className="grid-2">
        <Card title="Inject a failure" action={<span className="small dim">chaos engineering</span>}>
          <div className="stack" style={{ gap: 12 }}>
            <div className="field">
              <label htmlFor="sim-host">Target host</label>
              <select id="sim-host" className="input" value={hostname} onChange={(event) => setHostname(event.target.value)}>
                {(data?.hosts ?? [{ hostname: 'web-01' }]).map((host) => (
                  <option key={host.hostname} value={host.hostname}>
                    {host.hostname}
                  </option>
                ))}
              </select>
            </div>

            <div className="field">
              <label htmlFor="sim-metric">Fault type</label>
              <select id="sim-metric" className="input" value={metric} onChange={(event) => setMetric(event.target.value)}>
                {METRICS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label} ({item.hint})
                  </option>
                ))}
              </select>
            </div>

            <div className="row" style={{ gap: 12, alignItems: 'flex-end' }}>
              <div className="field grow">
                <label htmlFor="sim-value">Value {metric === 'down' ? '(ignored)' : ''}</label>
                <input
                  id="sim-value"
                  className="input"
                  value={value}
                  disabled={metric === 'down'}
                  onChange={(event) => setValue(event.target.value)}
                />
              </div>
              <div className="field grow">
                <label htmlFor="sim-duration">Duration (s)</label>
                <input
                  id="sim-duration"
                  className="input"
                  value={duration}
                  onChange={(event) => setDuration(event.target.value)}
                />
              </div>
            </div>

            <div className="toolbar">
              <button className="btn primary" onClick={() => void inject()} disabled={busy !== null || !reachable}>
                {busy === 'inject' ? 'Injecting…' : 'Inject fault'}
              </button>
              <button className="btn ghost" onClick={() => void clear()} disabled={busy !== null || !reachable}>
                {busy === 'clear' ? 'Clearing…' : 'Clear all'}
              </button>
              <span className="spacer grow" />
              <button className="btn" onClick={() => void runDetection()} disabled={busy !== null}>
                {busy === 'detect' ? 'Running…' : 'Run detection now'}
              </button>
            </div>

            {summary && (
              <div className="banner info" style={{ marginBottom: 0 }}>
                <div>
                  Cycle complete · {summary.hosts_evaluated} hosts · {summary.breaches} breaches ·{' '}
                  {summary.incidents_opened} opened · {summary.recoveries_triggered} recoveries ·{' '}
                  {summary.recoveries_blocked} blocked
                  {summary.errors.length > 0 && <div className="small">error: {summary.errors[0]}</div>}
                </div>
              </div>
            )}
          </div>
        </Card>

        <Card title="Virtual hosts" action={<span className="small dim">{data?.hosts.length ?? 0} targets</span>}>
          {data?.hosts.length === 0 && <EmptyState>Simulator not reachable.</EmptyState>}
          <div className="stack" style={{ gap: 12 }}>
            {data?.hosts.map((host) => (
              <div key={host.hostname} className="host-card" style={{ gap: 8 }}>
                <div className="host-head">
                  <span className="host-name">{host.hostname}</span>
                  <span className="spacer grow" />
                  <span className={`badge ${host.up ? 'ok' : 'crit'}`}>{host.up ? 'reachable' : 'down'}</span>
                </div>
                <div className="host-meta">{host.instance} · {host.role}</div>
                <div className="host-metrics">
                  {([
                    ['cpu', host.cpu_percent],
                    ['memory', host.memory_percent],
                    ['disk', host.disk_percent],
                  ] as const).map(([label, amount]) => (
                    <div className="metric-row" key={label}>
                      <span>{label.toUpperCase()}</span>
                      <Meter value={amount} />
                      <span className="value">{formatPercent(amount)}</span>
                    </div>
                  ))}
                </div>
                {host.faults.length > 0 && (
                  <div className="row" style={{ gap: 5 }}>
                    {host.faults.map((fault) => (
                      <span key={fault} className="badge warn">fault: {fault}</span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </Card>
      </div>

      <Card title="Active faults" action={<span className="small dim">auto-expire after the configured duration</span>}>
        {(data?.active_faults ?? []).length === 0 && <EmptyState>No faults injected. The fleet is nominal.</EmptyState>}
        {(data?.active_faults ?? []).length > 0 && (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Host</th>
                  <th>Metric</th>
                  <th>Value</th>
                  <th>Expires</th>
                  <th>In</th>
                </tr>
              </thead>
              <tbody>
                {data?.active_faults.map((fault) => (
                  <tr key={`${fault.hostname}-${fault.metric}`}>
                    <td>{fault.hostname}</td>
                    <td className="mono">{fault.metric}</td>
                    <td className="mono">{fault.value ?? '—'}</td>
                    <td className="small dim">{formatDateTime(fault.expires_at)}</td>
                    <td className="small">{timeAgo(fault.expires_at).replace(' ago', '')} left</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Test the loop">
        <ol className="small muted" style={{ margin: 0, paddingLeft: 18, lineHeight: 1.9 }}>
          <li>Inject CPU at 97% on <span className="mono">web-01</span> for 180 seconds.</li>
          <li>Wait for the next detection cycle (default 30s) — Prometheus fires <span className="mono">CpuSaturation</span>.</li>
          <li>Alertmanager posts to the backend; the classifier opens <span className="mono">INC-…</span> and dispatches
            <span className="mono"> recover_high_cpu.yml</span> through Ansible.</li>
          <li>Repeat attempts inside the cooldown window to watch the safeguards block the loop and log
            <span className="mono"> blocked</span> actions.</li>
          <li>Clear the fault; the incident auto-resolves after the settle window and MTTR is recorded.</li>
        </ol>
      </Card>
    </div>
  );
}
