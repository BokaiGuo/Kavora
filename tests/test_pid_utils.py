from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_stop_with_pid_file_waits_for_exit(tmp_path) -> None:
    proc = subprocess.Popen(["bash", "-lc", "sleep 30"], cwd=ROOT)
    pid_file = tmp_path / "sleep.pid"
    pid_file.write_text(str(proc.pid), encoding="utf-8")

    subprocess.run(
        [
            "bash",
            "-lc",
            f"source scripts/lib/pid_utils.sh && STOP_WAIT_MAX_S=5 stop_with_pid_file '{pid_file}' 'sleep'",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    proc.wait(timeout=5)
    assert proc.returncode is not None
    assert not pid_file.exists()
