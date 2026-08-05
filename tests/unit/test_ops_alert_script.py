"""Syntax-check the ops_alert shell helper (no gh execution — that needs a token)."""

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "ops_alert.sh"


def test_ops_alert_script_exists():
    assert SCRIPT.is_file(), f"missing {SCRIPT}"


def test_ops_alert_script_bash_syntax():
    proc = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_ops_alert_script_requires_two_args():
    # With no args and set -u, the script must exit non-zero before calling gh.
    proc = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True)
    assert proc.returncode != 0
