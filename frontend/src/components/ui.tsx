import { useEffect, useState, type ReactNode } from 'react';
import type { HostStatus, Severity } from '../lib/api';
import { formatPercent } from '../lib/format';

/* ------------------------------------------------------------------ badges */
export function SeverityBadge({ severity }: { severity: Severity | string }) {
  const cls = severity === 'critical' ? 'crit' : severity === 'warning' ? 'warn' : 'info';
  return <span className={`badge ${cls}`}>{severity}</span>;
}

export function StatusBadge({ status }: { status: HostStatus | string }) {
  const map: Record<string, string> = {
    up: 'ok',
    degraded: 'warn',
    down: 'crit',
    unknown: 'muted',
  };
  return (
    <span className={`badge ${map[status] ?? 'muted'}`}>
      <i className={`dot ${status === 'down' ? 'pulse' : ''}`} />
      {status}
    </span>
  );
}

export function RecoveryBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    success: 'ok',
    simulated: 'info',
    running: 'info',
    pending: 'muted',
    failed: 'crit',
    timeout: 'crit',
    blocked: 'warn',
  };
  return (
    <span className={`badge ${map[status] ?? 'muted'}`}>
      <i className={`dot ${status === 'running' ? 'pulse' : ''}`} />
      {status === 'simulated' ? 'simulated (dry-run)' : status}
    </span>
  );
}

export function IncidentStatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    open: 'crit',
    acknowledged: 'warn',
    recovering: 'info',
    resolved: 'ok',
    suppressed: 'muted',
  };
  return <span className={`badge ${map[status] ?? 'muted'}`}>{status}</span>;
}

/* ------------------------------------------------------------------- cards */
export function Card({
  title,
  action,
  children,
  className = '',
}: {
  title?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {(title || action) && (
        <header className="card-header">
          {title && <h2>{title}</h2>}
          <span className="spacer" />
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

export function StatCard({
  label,
  value,
  unit,
  hint,
  tone = 'default',
}: {
  label: string;
  value: string | number;
  unit?: string;
  hint?: string;
  tone?: 'default' | 'ok' | 'warn' | 'crit';
}) {
  return (
    <div className={`kpi ${tone === 'default' ? '' : tone}`}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">
        {value}
        {unit && <small>{unit}</small>}
      </div>
      {hint && <div className="kpi-hint">{hint}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ meters */
export function Meter({ value, max = 100 }: { value: number; max?: number }) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  const tone = pct >= 90 ? 'crit' : pct >= 75 ? 'warn' : '';
  return (
    <div className="meter" role="meter" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}>
      <span className={tone} style={{ width: `${pct}%` }} />
    </div>
  );
}

/* ------------------------------------------------------------------- chart */
export function Sparkline({ values, stroke = '#38bdf8' }: { values: number[]; stroke?: string }) {
  if (values.length < 2) {
    return <svg className="spark" viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden />;
  }
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const span = max - min || 1;
  const step = 100 / (values.length - 1);
  const points = values.map((value, index) => {
    const x = index * step;
    const y = 38 - ((value - min) / span) * 34;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  });
  const area = `0,40 ${points.join(' ')} 100,40`;
  return (
    <svg className="spark" viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden>
      <polygon points={area} fill={stroke} opacity="0.1" />
      <polyline points={points.join(' ')} fill="none" stroke={stroke} strokeWidth="1.6" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export function Chart({
  series,
  height = 160,
  unit = '%',
}: {
  series: Array<{ label: string; color: string; values: number[] }>;
  height?: number;
  unit?: string;
}) {
  const all = series.flatMap((item) => item.values);
  if (all.length < 2) return <div className="empty">Not enough samples yet — metrics appear after the first detection cycle.</div>;
  const max = Math.max(...all, 10);
  const min = Math.min(...all, 0);
  const span = max - min || 1;
  const count = Math.max(...series.map((item) => item.values.length));
  const step = count > 1 ? 100 / (count - 1) : 100;

  return (
    <div>
      <svg viewBox={`0 0 100 ${height / 4}`} preserveAspectRatio="none" style={{ width: '100%', height }} aria-hidden>
        {[0, 0.25, 0.5, 0.75, 1].map((fraction) => (
          <line
            key={fraction}
            x1="0"
            x2="100"
            y1={(height / 4) * fraction * 0.9 + 2}
            y2={(height / 4) * fraction * 0.9 + 2}
            stroke="rgba(255,255,255,0.06)"
            strokeWidth="1"
            vectorEffect="non-scaling-stroke"
          />
        ))}
        {series.map((item) => {
          const points = item.values.map((value, index) => {
            const x = index * step;
            const y = (height / 4) * 0.9 + 2 - ((value - min) / span) * ((height / 4) * 0.85);
            return `${x.toFixed(2)},${y.toFixed(2)}`;
          });
          return (
            <polyline
              key={item.label}
              points={points.join(' ')}
              fill="none"
              stroke={item.color}
              strokeWidth="1.7"
              vectorEffect="non-scaling-stroke"
            />
          );
        })}
      </svg>
      <div className="row small dim" style={{ marginTop: 6, gap: 14 }}>
        {series.map((item) => (
          <span key={item.label} className="row" style={{ gap: 5 }}>
            <i style={{ width: 8, height: 8, borderRadius: 2, background: item.color, display: 'inline-block' }} />
            {item.label}
            <span className="mono">{item.values.length ? formatPercent(item.values[item.values.length - 1]) : '—'}</span>
          </span>
        ))}
        <span className="right grow dim">scale {min.toFixed(0)}–{max.toFixed(0)}{unit}</span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ states */
export function EmptyState({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Spinner() {
  return <div className="spinner" role="status" aria-label="loading" />;
}

export function Switch({
  checked,
  onChange,
  label,
  disabled = false,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label?: string;
  disabled?: boolean;
}) {
  return (
    <label className="switch" aria-disabled={disabled}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span className="track" />
      {label}
    </label>
  );
}

/* ------------------------------------------------------------------- toast */
export type ToastState = { message: string; tone: 'ok' | 'error' } | null;

export function useToast() {
  const [toast, setToast] = useState<ToastState>(null);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 4200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  return {
    toast,
    notify: (message: string, tone: 'ok' | 'error' = 'ok') => setToast({ message, tone }),
  };
}

export function Toast({ toast }: { toast: ToastState }) {
  if (!toast) return null;
  return <div className={`toast ${toast.tone}`}>{toast.message}</div>;
}
