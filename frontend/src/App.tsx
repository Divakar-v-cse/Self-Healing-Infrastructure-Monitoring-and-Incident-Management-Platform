import { useState } from 'react';
import { api, type Host } from './lib/api';
import { usePolling } from './lib/usePolling';
import { HostDrawer, IncidentDrawer } from './components/drawers';
import { Toast, useToast } from './components/ui';
import Overview from './pages/Overview';
import Incidents from './pages/Incidents';
import Hosts from './pages/Hosts';
import Recoveries from './pages/Recoveries';
import Reports from './pages/Reports';
import Settings from './pages/Settings';
import Simulation from './pages/Simulation';

type TabKey = 'overview' | 'incidents' | 'hosts' | 'recoveries' | 'reports' | 'settings' | 'simulation';

const TABS: Array<{ key: TabKey; label: string; icon: string; title: string; subtitle: string }> = [
  {
    key: 'overview',
    label: 'Overview',
    icon: '◉',
    title: 'Operations overview',
    subtitle: 'Fleet health, active incidents and automated recovery activity',
  },
  {
    key: 'incidents',
    label: 'Incidents',
    icon: '▲',
    title: 'Incident management',
    subtitle: 'Detected, classified and reconciled infrastructure incidents',
  },
  { key: 'hosts', label: 'Hosts', icon: '▤', title: 'Host inventory', subtitle: 'Monitored servers, exporters and resource usage' },
  {
    key: 'recoveries',
    label: 'Recoveries',
    icon: '↻',
    title: 'Recovery log',
    subtitle: 'Every Ansible-driven action, including blocked attempts',
  },
  { key: 'reports', label: 'Reports', icon: '▦', title: 'Health reports', subtitle: 'Point-in-time snapshots with MTTR and success rates' },
  {
    key: 'settings',
    label: 'Settings',
    icon: '⚙',
    title: 'Thresholds & safeguards',
    subtitle: 'Tune detection rules and loop-prevention limits',
  },
  {
    key: 'simulation',
    label: 'Simulation',
    icon: '⚡',
    title: 'Failure simulation',
    subtitle: 'Inject faults and verify detection plus recovery end to end',
  },
];

export default function App() {
  const [tab, setTab] = useState<TabKey>('overview');
  const [incidentId, setIncidentId] = useState<number | null>(null);
  const [host, setHost] = useState<Host | null>(null);
  const { toast, notify } = useToast();

  const summary = usePolling(() => api.overview(), 20000);
  const safeguards = usePolling(() => api.safeguards(), 0);

  const active = TABS.find((item) => item.key === tab) ?? TABS[0];
  const openIncidents = summary.data?.incidents.open ?? 0;
  const dryRun = safeguards.data?.ansible_dry_run ?? true;

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">SH</div>
          <div className="brand-text">
            <strong>Self-Healing Infra</strong>
            <span>monitoring · incident · recovery</span>
          </div>
        </div>

        <nav className="nav">
          {TABS.map((item) => (
            <button
              key={item.key}
              className={tab === item.key ? 'active' : ''}
              onClick={() => setTab(item.key)}
            >
              <span className="nav-icon">{item.icon}</span>
              <span>{item.label}</span>
              {item.key === 'incidents' && openIncidents > 0 && <span className="nav-count">{openIncidents}</span>}
            </button>
          ))}
        </nav>

        <div className="sidebar-footer">
          <a href="http://localhost:3000" target="_blank" rel="noreferrer">Grafana dashboards ↗</a>
          <a href="http://localhost:9090" target="_blank" rel="noreferrer">Prometheus ↗</a>
          <a href="http://localhost:9093" target="_blank" rel="noreferrer">Alertmanager ↗</a>
          <a href="http://localhost:8000/docs" target="_blank" rel="noreferrer">API docs ↗</a>
          <span className="mono">v1.0.0 · {safeguards.data?.environment ?? 'unknown'}</span>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div>
            <h1>{active.title}</h1>
            <div className="subtitle">{active.subtitle}</div>
          </div>
          <div className="topbar-right">
            <span className="badge info">
              <i className="live-dot" /> live
            </span>
            <span className="small dim">auto-refresh 15s</span>
          </div>
        </header>

        {dryRun && (
          <div className="banner warn">
            <span>⚠</span>
            <div>
              Ansible recovery is in <strong>dry-run</strong> mode: playbooks are validated and actions are recorded as
              <span className="mono"> simulated</span>, but no host is modified. Set{' '}
              <span className="mono">SHIP_ANSIBLE_DRY_RUN=false</span> with a real inventory to execute recovery.
            </div>
          </div>
        )}

        {tab === 'overview' && (
          <Overview onOpenIncident={setIncidentId} onOpenHost={setHost} />
        )}
        {tab === 'incidents' && (
          <Incidents
            notify={notify}
            onOpenIncident={setIncidentId}
            onChanged={() => {
              summary.refresh();
            }}
          />
        )}
        {tab === 'hosts' && <Hosts onOpenHost={setHost} />}
        {tab === 'recoveries' && <Recoveries onOpenIncident={setIncidentId} />}
        {tab === 'reports' && <Reports notify={notify} />}
        {tab === 'settings' && <Settings notify={notify} />}
        {tab === 'simulation' && <Simulation notify={notify} />}
      </main>

      {incidentId !== null && (
        <IncidentDrawer
          incidentId={incidentId}
          onClose={() => setIncidentId(null)}
          onChanged={() => summary.refresh()}
          notify={notify}
        />
      )}

      {host && <HostDrawer host={host} onClose={() => setHost(null)} notify={notify} />}

      <Toast toast={toast} />
    </div>
  );
}

