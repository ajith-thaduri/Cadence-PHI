"""Console settings: configs/ui/ui.yaml + allow-listed environment overrides, validated strictly."""
from __future__ import annotations

import ipaddress
import os
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from harness.paths import ROOT

DEFAULT_FILE = ROOT / "configs" / "ui" / "ui.yaml"
Role = Literal["viewer", "operator", "admin"]
ROLE_RANK = {"viewer": 0, "operator": 1, "admin": 2}


class _S(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuthSettings(_S):
    mode: Literal["none", "token", "users"] = "none"
    token_env: str = "PHI_UI_TOKEN"
    token_role: Role = "admin"
    users_file: str = "configs/ui/users.yaml"
    session_hours: float = Field(8, gt=0, le=72)
    session_secret_env: str = "PHI_UI_SESSION_SECRET"
    cookie_secure: Literal["auto", "always", "never"] = "auto"
    max_login_failures_per_minute: int = Field(5, ge=1, le=100)


class RoleSettings(_S):
    analyze: Role = "operator"
    batch: Role = "operator"
    run_jobs: Role = "operator"
    run_test_data_jobs: Role = "admin"
    restore: Role = "admin"
    view_audit: Role = "admin"


class FeatureSettings(_S):
    what_if: bool = True
    restore: bool = True


class LimitSettings(_S):
    max_text_chars: int = Field(50_000, ge=100, le=1_000_000)
    max_upload_mb: int = Field(20, ge=1, le=500)
    max_batch_files: int = Field(200, ge=1, le=10_000)
    max_concurrent_jobs: int = Field(1, ge=1, le=8)
    what_if_engines: int = Field(2, ge=0, le=8)


class StorageSettings(_S):
    jobs_dir: str = "results/ui_jobs"
    audit_log: str = "results/ui_audit.jsonl"


class UISettings(_S):
    host: str = "127.0.0.1"
    port: int = Field(8765, ge=1, le=65535)
    engine_env: Literal["base", "dev", "test", "prod"] = "dev"
    device: Literal["auto", "cpu", "mps", "cuda"] = "auto"
    warm_start: bool = True
    auth: AuthSettings = Field(default_factory=AuthSettings)
    roles: RoleSettings = Field(default_factory=RoleSettings)
    features: FeatureSettings = Field(default_factory=FeatureSettings)
    limits: LimitSettings = Field(default_factory=LimitSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)

    @field_validator("host")
    @classmethod
    def _host(cls, v: str) -> str:
        if not v or any(c.isspace() for c in v):
            raise ValueError("host must be a hostname or IP address")
        return v

    @property
    def loopback(self) -> bool:
        if self.host in ("localhost",):
            return True
        try:
            return ipaddress.ip_address(self.host).is_loopback
        except ValueError:
            return False

    def path(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else ROOT / p

    def check_safe(self) -> None:
        """Refuse configurations that would expose the console without authentication."""
        if self.auth.mode == "none" and not self.loopback:
            raise ValueError(f"auth.mode 'none' is only allowed on a loopback host (got host {self.host!r}); "
                             "set auth.mode to 'token' or 'users' to serve on a network interface")
        if self.auth.mode == "token":
            tok = os.environ.get(self.auth.token_env, "")
            if len(tok) < 24:
                raise ValueError(f"auth.mode 'token' needs ${self.auth.token_env} set to at least 24 characters")
        if self.auth.mode == "users" and not self.path(self.auth.users_file).exists():
            raise ValueError(f"auth.mode 'users' needs {self.auth.users_file} "
                             "(create it with: python -m harness_ui user add <name> --role admin)")


ENV_OVERRIDES = {"PHI_UI_HOST": ("host", str), "PHI_UI_PORT": ("port", int), "PHI_UI_ENV": ("engine_env", str),
                 "PHI_UI_DEVICE": ("device", str), "PHI_UI_AUTH_MODE": ("auth.mode", str)}


def load(path: Optional[str | Path] = None) -> UISettings:
    p = Path(path) if path else DEFAULT_FILE
    raw = (yaml.safe_load(p.read_text()) or {}).get("ui", {}) if p.exists() else {}
    for env, (key, typ) in ENV_OVERRIDES.items():
        if env in os.environ:
            d = raw
            *parents, leaf = key.split(".")
            for k in parents:
                d = d.setdefault(k, {})
            d[leaf] = typ(os.environ[env])
    return UISettings.model_validate(raw)
