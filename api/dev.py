"""One-command local development launcher for the API and web client."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import List, Sequence


def development_commands(root: Path) -> Sequence[List[str]]:
    """Return explicit backend and frontend commands for a source checkout."""

    npm = shutil.which("npm")
    if npm is None:
        raise RuntimeError("npm is required to run the LoadOut web client")
    if not (root / "web" / "package.json").is_file():
        raise RuntimeError("loadout-dev must be run from an editable source checkout")
    return (
        [sys.executable, "-m", "uvicorn", "api.app:app", "--reload"],
        [npm, "run", "dev", "--prefix", str(root / "web")],
    )


def run() -> int:
    """Run both development services and stop both when either one exits."""

    root = Path(__file__).resolve().parents[1]
    try:
        commands = development_commands(root)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    processes = [subprocess.Popen(command, cwd=str(root)) for command in commands]
    exit_code = 0
    try:
        while True:
            for process in processes:
                result = process.poll()
                if result is not None:
                    exit_code = result
                    return exit_code
            time.sleep(0.2)
    except KeyboardInterrupt:
        return 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
