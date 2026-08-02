"""Finding the AArch64 toolchain, and running an image under QEMU.

Two test modules need this -- the backend's own suite and the differential
fuzzer -- and the second one came later. Copying the discovery into it would
have been three lines and the beginning of the failure this compiler was
rewritten to avoid: two copies of one thing, one of which gets the next fix.

Nothing here is a fixture, because the toolchain's absence decides whether
tests are COLLECTED, and that is a decision made at import time.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

#: Neither the Arm GNU Toolchain nor QEMU puts itself on PATH after an unzip
#: on Windows, so the usual install locations are checked too. The environment
#: variable takes precedence: guessing at install paths is a convenience for
#: the common case and must not be the only way in.
_TOOLCHAIN_DIRS = (
    Path(os.environ["ASMPYTHON_AARCH64_BIN"]),
) if os.environ.get("ASMPYTHON_AARCH64_BIN") else (
    Path(r"C:\tools\aarch64-none-elf\bin"),
    Path("/opt/aarch64-none-elf/bin"),
    Path("/usr/bin"),
)
_QEMU_DIRS = (
    Path(os.environ["ASMPYTHON_QEMU_BIN"]),
) if os.environ.get("ASMPYTHON_QEMU_BIN") else (
    Path(r"C:\Program Files\qemu"),
    Path("/usr/bin"),
)


def _find(names: tuple[str, ...], extra: tuple[Path, ...]) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
        for directory in extra:
            for suffix in ("", ".exe"):
                candidate = directory / (name + suffix)
                if candidate.is_file():
                    return str(candidate)
    return None


CC = _find(("aarch64-none-elf-gcc", "aarch64-elf-gcc"), _TOOLCHAIN_DIRS)
QEMU = _find(("qemu-system-aarch64",), _QEMU_DIRS)

AVAILABLE = bool(CC and QEMU)
REASON = "needs aarch64-none-elf-gcc and qemu-system-aarch64"


class on_path:
    """Put the cross toolchain on PATH for the duration of a link.

    The link stage looks the compiler up by the name the target gives, through
    the ordinary PATH search, because that is how a user's machine works. On a
    machine where the toolchain was unzipped rather than installed, the tests
    know where it is and the link stage does not.
    """

    def __enter__(self) -> None:
        self._saved = os.environ.get("PATH", "")
        if CC:
            os.environ["PATH"] = str(Path(CC).parent) + os.pathsep + self._saved

    def __exit__(self, *exc) -> None:
        os.environ["PATH"] = self._saved


def run_image(image: Path, *, timeout: int = 120) -> list[str]:
    """Boot a freestanding image and return what it printed.

    `-M virt` with no guest OS: the image is the kernel, and its UART writes
    land on stdout. It exits by itself through PSCI SYSTEM_OFF -- an image
    that parks in `wfi` instead runs until this timeout, which turns a
    quarter-second program into a two-minute one and a suite into a coffee
    break.
    """
    ran = subprocess.run(
        [QEMU, "-M", "virt", "-cpu", "cortex-a57", "-nographic",
         "-kernel", str(image)],
        capture_output=True, text=True, timeout=timeout)
    # Only the trailing empty entry from the final newline is dropped. A blank
    # line in the MIDDLE is output -- `print()` produces one -- and filtering
    # every empty line made that case silently pass.
    lines = [line.rstrip("\r") for line in ran.stdout.split("\n")]
    if lines and lines[-1] == "":
        lines.pop()
    return lines
