"""Manage API/worker/beat on Linux/WSL; PostgreSQL and Redis must already run."""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".local/processes.json"
COMMANDS = {
    "api": [
        "uvicorn",
        "orderflow.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8001",
        "--no-access-log",
    ],
    "worker": [
        "celery",
        "-A",
        "orderflow.workers.celery_app:app",
        "worker",
        "--loglevel=WARNING",
        "--concurrency=2",
    ],
    "beat": [
        "celery",
        "-A",
        "orderflow.workers.celery_app:app",
        "beat",
        "--loglevel=WARNING",
        "--schedule=.local/celerybeat-schedule",
    ],
}


def alive(pid):
    try:
        command = Path(f"/proc/{pid}/cmdline").read_bytes()
        return b"orderflow." in command and str(ROOT).encode() in command
    except FileNotFoundError:
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "status"])
    args = parser.parse_args()
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    if args.action == "stop":
        for name, pid in state.items():
            if alive(pid):
                os.killpg(pid, signal.SIGTERM)
                print(f"Stopping {name}")
        deadline = time.monotonic() + 10
        while any(alive(pid) for pid in state.values()) and time.monotonic() < deadline:
            time.sleep(0.1)
        if any(alive(pid) for pid in state.values()):
            raise SystemExit("Graceful shutdown is still in progress; run stop again shortly")
        STATE.unlink(missing_ok=True)
        return
    if args.action == "status":
        print(
            json.dumps(
                {name: {"pid": pid, "running": alive(pid)} for name, pid in state.items()}, indent=2
            )
        )
        return
    STATE.parent.mkdir(parents=True, exist_ok=True)
    for name, command in COMMANDS.items():
        if name in state and alive(state[name]):
            continue
        with (STATE.parent / f"{name}.log").open("a") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", *command],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        state[name] = process.pid
        STATE.write_text(json.dumps(state))
        time.sleep(0.3)
        if process.poll() is not None:
            raise SystemExit(f"{name} failed; see .local/{name}.log")
    for _ in range(30):
        try:
            with urlopen("http://127.0.0.1:8001/health/ready", timeout=1) as result:
                if result.status == 200:
                    print("OrderFlow ready: http://127.0.0.1:8001/docs")
                    return
        except OSError:
            time.sleep(0.3)
    raise SystemExit("Readiness timeout; see .local/api.log")


if __name__ == "__main__":
    main()
