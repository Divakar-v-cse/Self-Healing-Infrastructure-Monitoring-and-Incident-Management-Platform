import { useEffect, useState } from 'react';
import { api, type SafeguardConfig, type Threshold } from '../lib/api';
import { titleCase } from '../lib/format';
import { usePolling } from '../lib/usePolling';
import { Card, EmptyState, Switch } from '../components/ui';

function ThresholdRow({
  threshold,
  onSave,
  notify,
}: {
  threshold: Threshold;
  onSave: () => void;
  notify: (message: string, tone?: 'ok' | 'error') => void;
}) {
  const [warning, setWarning] = useState(String(threshold.warning));
  const [critical, setCritical] = useState(String(threshold.critical));
  const [duration, setDuration] = useState(String(threshold.duration_seconds));
  const [enabled, setEnabled] = useState(threshold.enabled);
  const [autoRecovery, setAutoRecovery] = useState(threshold.auto_recovery);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setWarning(String(threshold.warning));
    setCritical(String(threshold.critical));
    setDuration(String(threshold.duration_seconds));
    setEnabled(threshold.enabled);
    setAutoRecovery(threshold.auto_recovery);
  }, [threshold]);

  const save = async () => {
    setSaving(true);
    try {
      await api.updateThreshold(threshold.metric, {
        warning: Number(warning),
        critical: Number(critical),
        duration_seconds: Number(duration),
        enabled,
        auto_recovery: autoRecovery,
      });
      notify(`${threshold.display_name} thresholds saved`, 'ok');
      onSave();
    } catch (err) {
      notify(err instanceof Error ? err.message : 'Could not save threshold', 'error');
    } finally {
      setSaving(false);
    }
  };

  const unitLabel = threshold.unit === 'bps' ? 'bit/s' : threshold.unit === 'ratio' ? 'ratio' : '%';

  return (
    <tr>
      <td>
        <div style={{ fontWeight: 600 }}>{threshold.display_name}</div>
        <div className="small dim mono">{threshold.metric} {threshold.comparator} threshold · {unitLabel}</div>
      </td>
      <td>
        <input
          className="input sm"
          value={warning}
          onChange={(event) => setWarning(event.target.value)}
          inputMode="decimal"
        />
      </td>
      <td>
        <input
          className="input sm"
          value={critical}
          onChange={(event) => setCritical(event.target.value)}
          inputMode="decimal"
        />
      </td>
      <td>
        <input
          className="input sm"
          value={duration}
          onChange={(event) => setDuration(event.target.value)}
          inputMode="numeric"
        />
      </td>
      <td><Switch checked={enabled} onChange={setEnabled} /></td>
      <td><Switch checked={autoRecovery} onChange={setAutoRecovery} /></td>
      <td className="right">
        <button className="btn sm" onClick={() => void save()} disabled={saving}>
          {saving ? 'Saving…' : 'Save'}
        </button>
      </td>
    </tr>
  );
}

const NUMERIC_FIELDS: Array<{ key: keyof SafeguardConfig; label: string; hint: string; suffix?: string }> = [
  { key: 'max_attempts_per_incident', label: 'Max attempts per incident', hint: 'hard stop per incident before escalation' },
  { key: 'max_attempts_per_window', label: 'Max attempts per window', hint: 'per host + action inside the window' },
  { key: 'attempt_window_minutes', label: 'Attempt window', hint: 'sliding window length', suffix: 'min' },
  { key: 'recovery_cooldown_seconds', label: 'Cooldown between attempts', hint: 'minimum gap for the same host + action', suffix: 's' },
  { key: 'circuit_breaker_threshold', label: 'Circuit breaker threshold', hint: 'consecutive failures before blocking' },
  { key: 'circuit_breaker_cooldown_seconds', label: 'Breaker cooldown', hint: 'how long a tripped breaker stays open', suffix: 's' },
  { key: 'max_concurrent_recoveries', label: 'Max concurrent recoveries', hint: 'global fan-out limit' },
  { key: 'incident_autoresolve_minutes', label: 'Auto-resolve settle window', hint: 'time below threshold before closing', suffix: 'min' },
];

export default function Settings({ notify }: { notify: (message: string, tone?: 'ok' | 'error') => void }) {
  const thresholds = usePolling(() => api.thresholds(), 0);
  const safeguards = usePolling(() => api.safeguards(), 0);
  const [form, setForm] = useState<SafeguardConfig | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (safeguards.data) setForm(safeguards.data);
  }, [safeguards.data]);

  const saveSafeguards = async () => {
    if (!form) return;
    setSaving(true);
    try {
      const updated = await api.updateSafeguards(form);
      setForm(updated);
      notify('Safeguard configuration saved', 'ok');
    } catch (err) {
      notify(err instanceof Error ? err.message : 'Could not save safeguards', 'error');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="stack">
      <Card
        title="Detection thresholds"
        action={<span className="small dim">changes apply on the next detection cycle</span>}
      >
        {thresholds.error && <div className="banner error">{thresholds.error}</div>}
        {!thresholds.data && <EmptyState>Loading thresholds…</EmptyState>}
        {thresholds.data && (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Metric</th>
                  <th>Warning</th>
                  <th>Critical</th>
                  <th>Duration (s)</th>
                  <th>Enabled</th>
                  <th>Auto-recovery</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {thresholds.data.map((threshold) => (
                  <ThresholdRow
                    key={threshold.id}
                    threshold={threshold}
                    notify={notify}
                    onSave={() => thresholds.refresh()}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="small dim" style={{ marginBottom: 0 }}>
          Threshold breaches must persist for the configured duration (tracked per host and metric) before an incident
          is opened. Alertmanager rules mirror these values so both detection paths agree.
        </p>
      </Card>

      <Card title="Recovery safeguards" action={<span className="small dim">loop &amp; storm protection</span>}>
        {!form && <EmptyState>Loading configuration…</EmptyState>}
        {form && (
          <>
            <div className="banner warn">
              <span>⚠</span>
              <div>
                Ansible execution is currently{' '}
                <strong>{form.ansible_dry_run ? 'simulated (dry-run)' : 'enabled for real hosts'}</strong>. Actions
                recorded as <span className="mono">simulated</span> verified the playbook exists but did not touch any
                machine.
              </div>
            </div>

            <div className="row" style={{ gap: 22, marginBottom: 16 }}>
              <Switch
                checked={form.recovery_enabled}
                label="Automated recovery enabled (global)"
                onChange={(next) => setForm({ ...form, recovery_enabled: next })}
              />
              <Switch
                checked={form.ansible_enabled}
                label="Ansible execution enabled"
                onChange={(next) => setForm({ ...form, ansible_enabled: next })}
              />
            </div>

            <div className="grid-3">
              {NUMERIC_FIELDS.map((field) => (
                <div className="field" key={field.key}>
                  <label htmlFor={field.key}>{field.label}{field.suffix ? ` (${field.suffix})` : ''}</label>
                  <input
                    id={field.key}
                    className="input"
                    type="number"
                    value={String(form[field.key])}
                    onChange={(event) =>
                      setForm({ ...form, [field.key]: Number(event.target.value) } as SafeguardConfig)
                    }
                  />
                  <span className="small dim">{field.hint}</span>
                </div>
              ))}
            </div>

            <div className="toolbar" style={{ marginTop: 16 }}>
              <button className="btn primary" onClick={() => void saveSafeguards()} disabled={saving}>
                {saving ? 'Saving…' : 'Save safeguards'}
              </button>
              <button className="btn ghost" onClick={() => safeguards.data && setForm(safeguards.data)} disabled={saving}>
                Reset
              </button>
            </div>

            <div className="drawer-section">
              <h3>Runtime paths</h3>
              <dl className="kv">
                <dt>Environment</dt>
                <dd>{titleCase(form.environment)}</dd>
                <dt>Playbook directory</dt>
                <dd className="mono small">{form.playbook_dir}</dd>
                <dt>Inventory</dt>
                <dd className="mono small">{form.inventory_file}</dd>
              </dl>
            </div>
          </>
        )}
      </Card>
    </div>
  );
}
