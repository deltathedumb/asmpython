"""Reference shim: run a case under the host CPython.

This is also the shim the self-test uses, which is what makes the suite
self-correcting -- CPython must score 100% on the counted tiers, and any
failure there is a bug in the SUITE rather than in an implementation.
"""
from __future__ import annotations

import subprocess
import sys


def run(case_path: str, timeout: int):
    """-> (stdout, stderr, returncode). returncode None means "refused to run".

    CPython never refuses, so this shim only ever returns a real exit code.
    """
    try:
        cp = subprocess.run(
            [sys.executable, case_path],
            capture_output=True,
            text=True,
            errors="replace",   # a case may legitimately print undecodable bytes
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise TimeoutError(case_path)
    return cp.stdout, cp.stderr, cp.returncode
