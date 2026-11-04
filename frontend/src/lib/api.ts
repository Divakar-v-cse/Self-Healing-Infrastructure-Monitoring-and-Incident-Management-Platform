/**
 * Typed API client. All calls go through the same origin so the Vite dev proxy
 * and the nginx reverse proxy both work without extra configuration.
 */

const BASE = import.meta.env.VITE_API_BASE ?? '/api/v1';

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
    this.name = 'ApiError';
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    ...init,
  });
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* keep the status text */
    }
    throw new ApiError(detail, response.status);
  }
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

const get = <T>(path: string) => request<T>(path);
const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) });
const patch = <T>(path: string, body: unknown) =>
  request<T>(path, { method: 'PATCH', body: JSON.stringify(body) });
const del = <T>(path: string) => request<T>(path, { method: 'DELETE' });

// --------------------------------------------------------------------------
// types
// --------------------------------------------------------------------------
export type Severity = 'info' | 'warning' | 'critical';
export type HostStatus = 'up' | 'down' | 'degraded' | 'unknown';
export type IncidentStatus = 'open' | 'acknowledged' | 'recovering' | 'resolved' | 'suppressed';
export type RecoveryStatus = 'pending' | 'running' | 'success' | 'failed' | 'simulated' | 'blocked' | 'timeout';

export interface Host {
  id: number;
  hostname: string;
  instance: string | null;
  ip_address: string | null;
  role: string;
  environment: string;
  os: string | null;
  status: HostStatus;
  maintenance_mode: boolean;
  auto_recovery_enabled: boolean;
  labels: Record<string, unknown>;
  last_seen: string | null;
  created_at: string;
  latest_metrics: Record<string, number>;
  open_incidents: number;
}

export interface MetricPoint {
  timestamp: string;
  value: number;
}

export interface MetricSeries {
  metric: string;
  unit: string;
  points: MetricPoint[];
}

export interface RecoveryAction {
  id: number;
  incident_id: number | null;
  host_id: number | null;
  action_type: string;
  playbook: string | null;
  status: RecoveryStatus;
  attempt: number;
  triggered_by: string;
  dry_run: boolean;
  safeguard_reason: string | null;
  command: string | null;
  output: string | null;
  duration_seconds: number | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

export interface Incident {
  id: number;
  reference: string;
  title: string;
  description: string | null;
  category: string;
  severity: Severity;
  status: IncidentStatus;
  host_id: number | null;
  fingerprint: string;
  metric: string | null;
  observed_value: number | null;
  threshold_value: number | null;
  classification_reason: string | null;
  recommended_playbook: string | null;
  root_cause: string | null;
  detected_at: string;
  acknowledged_at: string | null;
  resolved_at: string | null;
  mttr_seconds: number | null;
  auto_recovery_attempted: boolean;
  details: Record<string, unknown>;
  hostname: string | null;
}

export interface IncidentDetail extends Incident {
  recovery_actions: RecoveryAction[];
}

export interface IncidentList {
  items: Incident[];
  total: number;
  page: number;
  page_size: number;
}

export interface Alert {
  id: number;
  fingerprint: string;
  alertname: string;
  severity: Severity;
  status: 'firing' | 'resolved';
  host_id: number | null;
  instance: string | null;
  summary: string | null;
  description: string | null;
  labels: Record<string, string>;
  starts_at: string;
  ends_at: string | null;
  received_at: string;
  source: string;
}

export interface Threshold {
  id: number;
  metric: string;
  display_name: string;
  unit: string;
  warning: number;
  critical: number;
  comparator: string;
  duration_seconds: number;
  enabled: boolean;
  auto_recovery: boolean;
  category: string;
}

export interface SafeguardConfig {
  recovery_enabled: boolean;
  ansible_enabled: boolean;
  ansible_dry_run: boolean;
  max_attempts_per_incident: number;
  max_attempts_per_window: number;
  attempt_window_minutes: number;
  recovery_cooldown_seconds: number;
  circuit_breaker_threshold: number;
  circuit_breaker_cooldown_seconds: number;
  max_concurrent_recoveries: number;
  incident_autoresolve_minutes: number;
  environment: string;
  playbook_dir: string;
  inventory_file: string;
}

export interface EventLog {
  id: number;
  created_at: string;
  level: string;
  category: string;
  actor: string;
  message: string;
  host_id: number | null;
  incident_id: number | null;
  context: Record<string, unknown>;
}

export interface HealthReport {
  id: number;
  generated_at: string;
  window_start: string;
  window_end: string;
  hosts_total: number;
  hosts_up: number;
  hosts_down: number;
  availability_percent: number;
  avg_cpu_percent: number;
  avg_memory_percent: number;
  avg_disk_percent: number;
  incidents_total: number;
  incidents_critical: number;
  incidents_open: number;
  recoveries_total: number;
  recoveries_success: number;
  recovery_success_rate: number;
  mean_time_to_recovery_seconds: number | null;
  payload: {
    per_host?: Array<{
      hostname: string;
      status: string;
      maintenance_mode: boolean;
      incidents: number;
      critical_incidents: number;
    }>;
    incidents_by_category?: Record<string, number>;
    recoveries?: { total: number; executed: number; successful: number; failed: number; blocked: number };
    top_incidents?: Array<{
      reference: string;
      title: string;
      severity: string;
      host_id: number | null;
      status: string;
      detected_at: string | null;
      mttr_seconds: number | null;
    }>;
  };
}

export interface Kpi {
  value: number;
  unit: string;
  label: string;
}

export interface Overview {
  generated_at: string;
  hosts: Record<string, number>;
  alerts: { firing: number; critical: number };
  incidents: { open: number; critical: number; in_window: number; resolved_in_window: number };
  recoveries: { total: number; successful: number; failed: number; blocked: number; running: number };
  kpis: { availability: Kpi; mttr: Kpi; recovery_success: Kpi; open_incidents: Kpi };
  host_status: Host[];
}

export interface SimulationHost {
  hostname: string;
  instance: string;
  role: string;
  up: boolean;
  cpu_percent: number;
  memory_percent: number;
  disk_percent: number;
  network_mbps: number;
  faults: string[];
}

export interface SimulationStatus {
  simulator_reachable: boolean;
  mode: string;
  hosts: SimulationHost[];
  active_faults: Array<{ hostname: string; metric: string; value: number | null; expires_at: string }>;
  detail: string | null;
}

export interface DetectionSummary {
  hosts_evaluated: number;
  breaches: number;
  incidents_opened: number;
  incidents_refreshed: number;
  incidents_resolved: number;
  recoveries_triggered: number;
  recoveries_blocked: number;
  errors: string[];
}

export interface RecoveryResult {
  allowed: boolean;
  status: RecoveryStatus;
  reason: string | null;
  rule: string | null;
  action: RecoveryAction | null;
}

// --------------------------------------------------------------------------
// endpoints
// --------------------------------------------------------------------------
export const api = {
  overview: () => get<Overview>('/overview'),
  events: (limit = 40) => get<EventLog[]>(`/events?limit=${limit}`),

  hosts: () => get<Host[]>('/hosts'),
  host: (id: number) => get<Host>(`/hosts/${id}`),
  hostMetrics: (id: number, minutes = 60) => get<MetricSeries[]>(`/hosts/${id}/metrics?minutes=${minutes}`),
  updateHost: (id: number, body: Partial<Pick<Host, 'maintenance_mode' | 'auto_recovery_enabled' | 'role' | 'environment'>>) =>
    patch<Host>(`/hosts/${id}`, body),

  incidents: (query: Record<string, string | number | undefined> = {}) => {
    const params = new URLSearchParams();
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== '') params.set(key, String(value));
    });
    const suffix = params.toString();
    return get<IncidentList>(`/incidents${suffix ? `?${suffix}` : ''}`);
  },
  incident: (id: number) => get<IncidentDetail>(`/incidents/${id}`),
  createIncident: (body: Record<string, unknown>) => post<IncidentDetail>('/incidents', body),
  updateIncident: (id: number, body: Record<string, unknown>) => patch<IncidentDetail>(`/incidents/${id}`, body),
  recover: (id: number, body: { playbook?: string; reason?: string; force?: boolean } = {}) =>
    post<RecoveryResult>(`/incidents/${id}/recover`, body),

  alerts: (limit = 40) => get<Alert[]>(`/alerts?limit=${limit}`),
  recoveries: (limit = 100) => get<RecoveryAction[]>(`/recoveries?limit=${limit}`),

  thresholds: () => get<Threshold[]>('/thresholds'),
  updateThreshold: (metric: string, body: Partial<Threshold>) => patch<Threshold>(`/thresholds/${metric}`, body),

  safeguards: () => get<SafeguardConfig>('/safeguards'),
  updateSafeguards: (body: SafeguardConfig) => patch<SafeguardConfig>('/safeguards', body),

  reports: () => get<HealthReport[]>('/reports'),
  createReport: (hours = 24) => post<HealthReport>(`/reports?hours=${hours}`),

  runDetection: () => post<DetectionSummary>('/detections/run'),

  simulation: () => get<SimulationStatus>('/simulation'),
  injectFault: (body: { hostname: string; metric: string; value?: number | null; duration_seconds: number }) =>
    post<{ hostname: string; metric: string; expires_at: string | null }>('/simulation/faults', body),
  clearFaults: (hostname?: string) =>
    del<{ message: string }>(`/simulation/faults${hostname ? `?hostname=${hostname}` : ''}`),
};
