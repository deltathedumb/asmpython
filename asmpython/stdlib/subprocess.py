"""subprocess module: spawn subprocesses.

Uses os.system for simple invocation. Full Popen with pipes is not
available in the freestanding target (no stdin/stdout pipe FFI), but
the common call()/run()/check_output() API is provided.
"""
from __future__ import annotations

import os

PIPE: int = -1
STDOUT: int = -2
DEVNULL: int = -3
STD_INPUT_HANDLE: int = -10
STD_OUTPUT_HANDLE: int = -11
STD_ERROR_HANDLE: int = -12


class CalledProcessError(Exception):
    """Raised when a process exits with a non-zero return code."""

    def __init__(self, returncode: int, cmd: str, output: str = "",
                 stderr: str = "") -> None:
        self.returncode: int = returncode
        self.cmd: str = cmd
        self.output: str = output
        self.stderr: str = stderr

    def __str__(self) -> str:
        return ("Command '" + self.cmd + "' returned non-zero exit status " +
                str(self.returncode))


class TimeoutExpired(Exception):
    """Raised when a process times out."""

    def __init__(self, cmd: str, timeout: float) -> None:
        self.cmd: str = cmd
        self.timeout: float = timeout

    def __str__(self) -> str:
        return "Command '" + self.cmd + "' timed out after " + str(self.timeout) + "s"


class CompletedProcess:
    """Return value from run()."""

    def __init__(self, args: str, returncode: int,
                 stdout: str = "", stderr: str = "") -> None:
        self.args: str = args
        self.returncode: int = returncode
        self.stdout: str = stdout
        self.stderr: str = stderr

    def check_returncode(self) -> None:
        if self.returncode != 0:
            raise CalledProcessError(self.returncode, self.args)


class Popen:
    """Run a child program in a new process."""

    def __init__(self, args: str, stdin: int = 0, stdout: int = 0,
                 stderr: int = 0, shell: int = 0,
                 cwd: str = "", env: int = 0) -> None:
        self.args: str = args
        self.stdin: int = stdin
        self.stdout_fd: int = stdout
        self.stderr_fd: int = stderr
        self.returncode: int = -1
        self.pid: int = 0
        if shell:
            self.returncode = os.system(args)
        else:
            self.returncode = os.system(args)

    def communicate(self, input: str = "") -> list:
        """Send data to stdin and read stdout/stderr. Returns [stdout, stderr]."""
        return ["", ""]

    def wait(self, timeout: float = 0.0) -> int:
        """Wait for child process to terminate."""
        return self.returncode

    def poll(self) -> int:
        """Check if child process has terminated."""
        return self.returncode

    def kill(self) -> None:
        """Kill the child process."""
        pass

    def terminate(self) -> None:
        """Terminate the child process."""
        pass

    def send_signal(self, sig: int) -> None:
        """Send a signal to the process."""
        pass

    def __enter__(self) -> Popen:
        return self

    def __exit__(self, exc_type: int, exc_val: int, exc_tb: int) -> int:
        return 0


def run(args: str, stdin: int = 0, stdout: int = 0, stderr: int = 0,
        shell: int = 0, check: int = 0, timeout: float = 0.0,
        cwd: str = "", capture_output: int = 0) -> CompletedProcess:
    """Run a command, wait for it to complete, return CompletedProcess."""
    rc: int = os.system(args)
    cp: CompletedProcess = CompletedProcess(args, rc)
    if check and rc != 0:
        raise CalledProcessError(rc, args)
    return cp


def call(args: str, stdin: int = 0, stdout: int = 0, stderr: int = 0,
         shell: int = 0, timeout: float = 0.0, cwd: str = "") -> int:
    """Run command args. Wait for command to complete. Return returncode."""
    return os.system(args)


def check_call(args: str, stdin: int = 0, stdout: int = 0, stderr: int = 0,
               shell: int = 0, timeout: float = 0.0, cwd: str = "") -> int:
    """Run command and check for non-zero exit."""
    rc: int = os.system(args)
    if rc != 0:
        raise CalledProcessError(rc, args)
    return 0


def check_output(args: str, stdin: int = 0, stderr: int = 0,
                 shell: int = 0, timeout: float = 0.0,
                 cwd: str = "") -> str:
    """Run command and return its output (stub: returns empty string)."""
    rc: int = os.system(args)
    if rc != 0:
        raise CalledProcessError(rc, args)
    return ""


def getstatusoutput(cmd: str) -> list:
    """Return (status, output) of running cmd in shell (stub)."""
    rc: int = os.system(cmd)
    return [rc, ""]


def getoutput(cmd: str) -> str:
    """Return output of running cmd in shell (stub: returns empty string)."""
    os.system(cmd)
    return ""
