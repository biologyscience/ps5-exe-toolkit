# Hot-reload for app.py

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "../app.py"
POLL_SECONDS = 0.5


def snapshot():
    sources = (ROOT / "../app.py", *(ROOT / "util").glob("*.py"))
    return {path: path.stat().st_mtime for path in sources}


def start(first):
    print("[dev] starting app..." if first else "[dev] restarting app...")
    return subprocess.Popen([sys.executable, str(TARGET)])


def stop(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


def main():
    proc = start(first=True)
    last = snapshot()
    try:
        while True:
            time.sleep(POLL_SECONDS)
            current = snapshot()
            if current != last:
                last = current
                print("[dev] change detected")
                stop(proc)
                proc = start(first=False)
            elif proc.poll() is not None:
                if proc.returncode == 0:
                    break  # window was closed normally
                print(f"[dev] app exited with code {proc.returncode}; waiting for changes...")
                while snapshot() == last:
                    time.sleep(POLL_SECONDS)
                last = snapshot()
                proc = start(first=False)
    except KeyboardInterrupt:
        pass
    finally:
        stop(proc)


if __name__ == "__main__":
    main()