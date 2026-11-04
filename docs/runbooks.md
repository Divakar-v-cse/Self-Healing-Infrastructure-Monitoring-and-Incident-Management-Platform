# Operator runbooks

Each alert rule links here via `runbook_url`. Every section follows the same shape: what it means, how to
confirm, what the platform does automatically, and what you must do by hand.

---

## Host unreachable

**Alert:** `HostDown` (critical, `up{job="node"} == 0` for 1m)

**Means:** Prometheus cannot scrape the node exporter. Either the host, the network path, or the exporter
itself is down.

**Confirm**
```bash
ping -c 3 <host>
nc -vz <host> 9100
systemctl status node_exporter
```

**Automatic:** incident category `availability`, critical → `recover_service.yml` restarts the configured
unit and re-probes the exporter. Guarded by the cooldown and per-incident limits.

**Manual**
1. If ICMP fails and SSH fails, treat it as a hardware/hypervisor event: check the console, power state and
   the hosting provider's status page.
2. If SSH works but `:9100` does not, restart `node_exporter`.
3. If the host is intentionally offline, set **maintenance mode** in the console so recovery stops firing.

---

## CPU saturation

**Alert:** `HighCpuUsage` (warning, >75% for 5m), `CpuSaturation` (critical, >90% for 2m)

**Means:** sustained CPU pressure. Check whether it is a batch job, a runaway process, or genuine capacity
shortage before automating anything.

**Confirm**
```bash
uptime
top -b -n1 | head -20
pidstat -u 1 5
```

**Automatic:** `recover_high_cpu.yml` terminates *allow-listed* runaway processes (`dd`, `stress`, `stress-ng`,
`yes`) above `cpu_kill_percent`, restarts the mapped service, waits 15s and reports the new load. It never
kills an unknown process.

**Manual**
1. If the top consumer is not in the allow list, investigate before restarting: a restart can hide a leak.
2. Correlate with deploy times and cron windows (`journalctl --since "-1h"`).
3. If saturation is legitimate load, scale out instead of restarting.

---

## Memory pressure

**Alert:** `HighMemoryUsage` (warning, >80% for 5m), `MemoryPressure` (critical, >92% for 2m)

**Confirm**
```bash
free -m
vmstat 1 5
ps -eo pid,comm,%mem,rss --sort=-%mem | head
```

**Automatic:** `recover_high_memory.yml` syncs, drops clean page cache (`vm.drop_caches=1`), and restarts the
service only when utilisation is at or above 92%.

**Manual**
1. Watch `si/so` in `vmstat` — swap thrash explains latency spikes that pure percentages hide.
2. If RSS grows monotonically across restarts, you have a leak: capture a heap profile before restarting again.
3. Consider raising the container/host limit only after the leak is ruled out.

---

## Disk pressure

**Alert:** `DiskSpaceLow` (warning, >80%), `HostDiskFull` (critical, >90%)

**Confirm**
```bash
df -h
df -i                     # inode exhaustion looks identical to a full disk
du -xh --max-depth=2 / | sort -h | tail -20
journalctl --disk-usage
```

**Automatic:** `recover_disk_pressure.yml` runs `logrotate -f`, vacuums the journal to 200M, deletes rotated
logs older than 7 days and stale `/tmp` files. Docker pruning is **opt-in** (`docker_prune_enabled=true`)
because it deletes images.

**Manual**
1. Inode exhaustion is not fixed by deleting large files — find the directory with millions of small files.
2. Check for deleted-but-open files: `lsof +L1 | sort -k7 -n | tail`.
3. If a database volume is filling, the fix is retention policy, not cleanup.

---

## Network anomaly

**Alert:** `NetworkTrafficSpike` (warning, ingress >100 Mbit/s for 5m)

**Confirm**
```bash
ip -s link
sar -n DEV 1 5
ss -s
```

**Automatic:** `recover_network.yml` inspects interface counters, flushes the DNS cache and probes a known
IP. Bouncing the interface requires `network_restart_enabled=true` — it is disruptive and off by default.

**Manual**
1. Compare rx/tx symmetry: a large asymmetry usually means backup, replication or exfiltration.
2. Check `errors`/`dropped` counters: rising drops with normal throughput point at a duplex/NIC issue.
3. Correlate with deployments and scheduled jobs before escalating to the network team.

---

## Service unavailable

**Alert:** `ServiceDown`, `SelfHealingBackendDown` (critical)

**Confirm**
```bash
systemctl status <unit>
journalctl -u <unit> -n 100 --no-pager
curl -sf localhost:<port>/health
```

**Automatic:** `recover_service.yml` restarts the unit, waits for `active`, and probes the health URL if
configured.

**Manual**
1. If the unit restarts and dies again within a minute, disable automated recovery (maintenance mode) — the
   safeguard breaker will open after 3 failures anyway.
2. Read the exit code: `137` = OOM kill, `1` = config/startup error.
3. Check dependencies (database, cache, upstream APIs) before assuming the service itself is at fault.

---

## Recovery is blocked by safeguards

**Alert:** `RecoveryBlockedBySafeguard` (warning, >2 blocks in 15m)

**Means:** the guard is doing its job. Something keeps failing, and automation has stopped retrying.

**Do**
1. Open **Recoveries** and read `safeguard_reason` on the blocked rows.
2. `cooldown` → you are retrying too fast; wait or pass `force` with a documented reason.
3. `per_incident_limit` / `window_limit` → the playbook is not fixing the root cause. Escalate.
4. `circuit_breaker` → the breaker opens for `circuit_breaker_cooldown_seconds`. Investigate failures before
   it auto-closes; raising the threshold hides a real problem.
5. Never disable safeguards globally to "make it work" during an incident without an explicit decision from
   the incident commander.

---

## Platform itself is down

**Alert:** `SelfHealingBackendDown`

1. `docker compose ps` / `systemctl status ship-backend`.
2. Check logs for a failed migration or a locked database.
3. The detection loop is in-process: if the API is down, no incidents are being created. Prometheus and
   Alertmanager keep evaluating and will replay grouped alerts once the backend returns.
4. Incidents that were `recovering` when the process died stay that way by design — reconcile manually in the
   **Incidents** view.
