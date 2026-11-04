"""Central runtime configuration.

Every operational knob is environment driven so the same image can run as a
local lab, a staging stack or production. Defaults are deliberately safe:
recovery execution is enabled but Ansible runs in dry-run mode until
``SHIP_ANSIBLE_DRY_RUN=false`` is set explicitly.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _repo_root() -> Path:
    """Best-effort repository root discovery (works in Docker and locally)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "docker-compose.yml").exists() or (parent / "ansible").exists():
            return parent
    # backend/app/config.py -> repository root is two levels above backend/
    return here.parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SHIP_",
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- application -------------------------------------------------------
    app_name: str = "Self-Healing Infrastructure Monitoring Platform"
    api_prefix: str = "/api/v1"
    environment: str = "development"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173,http://localhost:3000,http://localhost:8080"

    # --- persistence -------------------------------------------------------
    database_url: str = "sqlite:///./data/ship.db"

    # --- integrations ------------------------------------------------------
    prometheus_url: str = "http://localhost:9090"
    alertmanager_url: str = "http://localhost:9093"
    simulator_url: str = "http://localhost:9101"
    request_timeout_seconds: float = 10.0

    # --- monitoring loop ---------------------------------------------------
    enable_monitor_loop: bool = True
    monitor_interval_seconds: int = 30
    metric_lookback_minutes: int = 5
    metric_retention_days: int = 7

    # --- recovery / ansible ------------------------------------------------
    recovery_enabled: bool = True
    ansible_enabled: bool = True
    ansible_dry_run: bool = True
    ansible_playbook_dir: str = ""
    ansible_inventory: str = ""
    ansible_binary: str = "ansible-playbook"
    ansible_timeout_seconds: int = 300
    ansible_extra_args: str = "-e ansible_python_interpreter=/usr/bin/python3"
    recovery_requested_by: str = "auto"

    # --- safeguards --------------------------------------------------------
    max_attempts_per_incident: int = 3
    max_attempts_per_window: int = 5
    attempt_window_minutes: int = 60
    recovery_cooldown_seconds: int = 300
    circuit_breaker_threshold: int = 3
    circuit_breaker_cooldown_seconds: int = 1800
    max_concurrent_recoveries: int = 2
    incident_autoresolve_minutes: int = 10
    max_open_incidents_per_host: int = 6

    @property
    def repo_root(self) -> Path:
        return _repo_root()

    @property
    def playbook_dir(self) -> Path:
        return Path(self.ansible_playbook_dir) if self.ansible_playbook_dir else self.repo_root / "ansible" / "playbooks"

    @property
    def inventory_file(self) -> Path:
        return Path(self.ansible_inventory) if self.ansible_inventory else self.repo_root / "ansible" / "inventory" / "hosts.ini"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
