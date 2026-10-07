"""Authentication, sessions and role checks for the console.

Modes (configs/ui/ui.yaml auth.mode):
  none   single local user (role admin) - only on a loopback host (settings.check_safe)
  token  one shared secret from the environment; the login page exchanges it for a session cookie
  users  configs/ui/users.yaml with scrypt password hashes and a role per user

Sessions are HMAC-signed, expiring cookies (HttpOnly, SameSite=Strict, Secure on https). Mutating requests that
rely on the cookie must carry the X-PHI-UI header: a cross-site page cannot add a custom header without a CORS
preflight, and the console allows no CORS - together with SameSite=Strict this is the CSRF defence.
Passwords and tokens are compared in constant time; failed logins are rate limited per client address.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Optional

import yaml
from fastapi import HTTPException, Request

from harness_ui.settings import ROLE_RANK, UISettings

COOKIE = "phi_ui_session"
CSRF_HEADER = "x-phi-ui"
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}


@dataclass(frozen=True)
class Principal:
    name: str
    role: str

    def allows(self, needed: str) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[needed]


# ---------------------------------------------------------------------------- password hashing (stdlib scrypt)
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return "scrypt${n}${r}${p}${s}${h}".format(**_SCRYPT, s=base64.b64encode(salt).decode(), h=base64.b64encode(h).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, s, h = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(s), dklen=32, n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(got, base64.b64decode(h))
    except (ValueError, TypeError):
        return False


def load_users(path) -> dict[str, dict]:
    if not path.exists():
        return {}
    return (yaml.safe_load(path.read_text()) or {}).get("users", {}) or {}


def save_users(path, users: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("# Console users - managed by `python -m harness_ui user ...`. scrypt hashes only; never commit.\n")
        yaml.safe_dump({"users": users}, f, sort_keys=True)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------- authenticator
class Authenticator:
    def __init__(self, settings: UISettings) -> None:
        self.s = settings
        secret = os.environ.get(settings.auth.session_secret_env, "")
        self._secret = secret.encode() if len(secret) >= 32 else secrets.token_bytes(32)
        self._failures: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    # sessions -----------------------------------------------------------------------------------------
    def issue(self, p: Principal) -> str:
        body = base64.urlsafe_b64encode(json.dumps(
            {"u": p.name, "r": p.role, "exp": int(time.time() + self.s.auth.session_hours * 3600)}).encode()).decode()
        sig = hmac.new(self._secret, body.encode(), hashlib.sha256).hexdigest()
        return f"{body}.{sig}"

    def _read(self, token: str) -> Optional[Principal]:
        try:
            body, sig = token.rsplit(".", 1)
        except ValueError:
            return None
        if not hmac.compare_digest(sig, hmac.new(self._secret, body.encode(), hashlib.sha256).hexdigest()):
            return None
        try:
            d = json.loads(base64.urlsafe_b64decode(body.encode()))
        except ValueError:
            return None
        if d.get("exp", 0) < time.time() or d.get("r") not in ROLE_RANK:
            return None
        if self.s.auth.mode == "users":            # a removed / demoted user loses access immediately
            u = load_users(self.s.path(self.s.auth.users_file)).get(d.get("u"))
            if not u or u.get("disabled"):
                return None
            return Principal(d["u"], u.get("role", "viewer"))
        return Principal(d["u"], d["r"])

    # login -------------------------------------------------------------------------------------------
    def _throttled(self, client: str) -> bool:
        now = time.time()
        with self._lock:
            q = self._failures[client]
            while q and q[0] < now - 60:
                q.popleft()
            return len(q) >= self.s.auth.max_login_failures_per_minute

    def _fail(self, client: str) -> None:
        with self._lock:
            self._failures[client].append(time.time())

    def login(self, client: str, username: str, secret: str) -> Principal:
        if self.s.auth.mode == "none":
            return Principal("local", "admin")
        if self._throttled(client):
            raise HTTPException(429, "too many failed sign-in attempts; wait a minute")
        if self.s.auth.mode == "token":
            expected = os.environ.get(self.s.auth.token_env, "")
            if expected and hmac.compare_digest(secret.encode(), expected.encode()):
                return Principal(username.strip()[:64] or "token-user", self.s.auth.token_role)
        else:
            u = load_users(self.s.path(self.s.auth.users_file)).get(username)
            if u and not u.get("disabled") and verify_password(secret, u.get("password", "")):
                return Principal(username, u.get("role", "viewer"))
            if not u:
                verify_password(secret, hash_password("timing-equaliser"))   # keep timing similar for unknown users
        self._fail(client)
        raise HTTPException(401, "invalid credentials")

    # per request -------------------------------------------------------------------------------------
    def principal(self, request: Request) -> Optional[Principal]:
        if self.s.auth.mode == "none":
            return Principal("local", "admin")
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer ") and self.s.auth.mode == "token":
            expected = os.environ.get(self.s.auth.token_env, "")
            if expected and hmac.compare_digest(auth[7:].strip().encode(), expected.encode()):
                return Principal("api-token", self.s.auth.token_role)
            return None
        tok = request.cookies.get(COOKIE)
        if not tok:
            return None
        p = self._read(tok)
        if p and request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CSRF_HEADER) != "1":
            raise HTTPException(403, "missing X-PHI-UI header")
        return p

    def cookie_secure(self, request: Request) -> bool:
        m = self.s.auth.cookie_secure
        return m == "always" or (m == "auto" and request.url.scheme == "https")
