"""python -m harness_ui <command>

  serve [--host H] [--port P] [--env dev] [--config configs/ui/ui.yaml]   start the console (default command)
  user add <name> --role viewer|operator|admin                           create / reset a user (password prompted)
  user disable <name> | user list
  openapi [--output frontend/openapi.json]                              export the API schema (frontend types)
  check                                                                  validate configs/ui/ui.yaml and exit
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from harness_ui import settings as S
from harness_ui.security import hash_password, load_users, save_users


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m harness_ui")
    ap.add_argument("--config", default=None, help="console settings file (default configs/ui/ui.yaml)")
    sub = ap.add_subparsers(dest="cmd")
    sv = sub.add_parser("serve")
    sv.add_argument("--host")
    sv.add_argument("--port", type=int)
    sv.add_argument("--env", choices=["base", "dev", "test", "prod"])
    sv.add_argument("--no-warm", action="store_true")
    u = sub.add_parser("user")
    usub = u.add_subparsers(dest="ucmd", required=True)
    ua = usub.add_parser("add")
    ua.add_argument("name")
    ua.add_argument("--role", choices=["viewer", "operator", "admin"], required=True)
    ud = usub.add_parser("disable")
    ud.add_argument("name")
    usub.add_parser("list")
    oa = sub.add_parser("openapi")
    oa.add_argument("--output", default="frontend/openapi.json")
    sub.add_parser("check")
    a = ap.parse_args(argv)
    cmd = a.cmd or "serve"
    if cmd == "serve":
        for flag, env in (("host", "PHI_UI_HOST"), ("port", "PHI_UI_PORT"), ("env", "PHI_UI_ENV")):
            if getattr(a, flag, None):
                os.environ[env] = str(getattr(a, flag))
    st = S.load(a.config)
    if cmd == "check":
        try:
            st.check_safe()
        except ValueError as e:
            print("NOT OK:", e)
            return 1
        print(json.dumps(st.model_dump(), indent=1))
        return 0
    if cmd == "user":
        path = st.path(st.auth.users_file)
        users = load_users(path)
        if a.ucmd == "list":
            for n, u_ in sorted(users.items()):
                print(f"{n:<24} {u_.get('role'):<9} {'DISABLED' if u_.get('disabled') else ''}")
            return 0
        if a.ucmd == "disable":
            if a.name not in users:
                print("no such user")
                return 1
            users[a.name]["disabled"] = True
            save_users(path, users)
            print("disabled", a.name)
            return 0
        pw = getpass.getpass("password (min 12 characters): ")
        if len(pw) < 12 or pw != getpass.getpass("repeat: "):
            print("passwords too short or do not match")
            return 1
        users[a.name] = {"role": a.role, "password": hash_password(pw)}
        save_users(path, users)
        print(f"saved {a.name} ({a.role}) to {path}")
        return 0
    if cmd == "openapi":
        from harness_ui.app import create_app
        app = create_app(st.model_copy(update={"auth": st.auth.model_copy(update={"mode": "none"}), "host": "127.0.0.1"}),
                         warm=False)
        Path(a.output).write_text(json.dumps(app.openapi(), indent=1))
        print("wrote", a.output)
        return 0
    import uvicorn
    from harness_ui.app import create_app
    try:
        app = create_app(st, warm=False if getattr(a, "no_warm", False) else None)
    except ValueError as e:
        print("refusing to start:", e)
        return 2
    scheme = "http"
    print(f"PHI engine console -> {scheme}://{st.host}:{st.port}  (engine env {st.engine_env}, auth {st.auth.mode})")
    uvicorn.run(app, host=st.host, port=st.port, log_level="info", access_log=True, server_header=False,
                proxy_headers=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
