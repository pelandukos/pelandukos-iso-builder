#!/usr/bin/env python3
"""A boot passes only if an explicitly specified guest serial marker is observed."""
import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.qemu import qemu_arguments
from core.util import digest, now


def smoke(iso, mode, marker, timeout=120):
    with tempfile.TemporaryDirectory(prefix="pelandukos-smoke-") as temporary:
        arguments, acceleration = qemu_arguments(iso, temporary, mode, memory=1024)
        started = time.monotonic()
        with tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen(["qemu-system-x86_64", *arguments], stdout=stderr, stderr=stderr,
                                       stdin=subprocess.DEVNULL, start_new_session=True)
            log = ""
            try:
                serial = Path(temporary) / "serial.log"
                while time.monotonic() - started < timeout:
                    if serial.exists():
                        log = serial.read_text(errors="replace")
                    if marker in log or process.poll() is not None:
                        break
                    time.sleep(0.2)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            stderr.seek(0)
            errors = stderr.read().decode(errors="replace")
            return {"iso": str(Path(iso).resolve()), "sha256": digest(iso), "mode": mode,
                    "time": now(), "acceleration": acceleration, "marker": marker,
                    "passed": marker in log, "serial_log": log, "qemu_log": errors,
                    "elapsed_seconds": round(time.monotonic() - started, 2)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("iso", type=Path)
    parser.add_argument("--mode", choices=["BIOS", "UEFI"], required=True)
    parser.add_argument("--marker", required=True, help="Expected serial text emitted by the guest")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--report", type=Path, required=True)
    arguments = parser.parse_args()
    result = smoke(arguments.iso, arguments.mode, arguments.marker, arguments.timeout)
    arguments.report.write_text(json.dumps(result, indent=2) + "\n")
    print(f"{arguments.mode}: {'PASS' if result['passed'] else 'FAIL'} ({result['elapsed_seconds']}s)")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
