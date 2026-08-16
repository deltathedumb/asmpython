"""Differentially test pytest repositories with CPython, asmpython, and pyinbin.

The scout can take explicit GitHub repositories or search GitHub for Python
projects that advertise pytest.  Each selected repository is cloned into an
isolated work directory, prepared in its own virtual environment, and run
through the same generated pytest launcher in three modes:

* CPython provides the interpreted baseline.
* asmpython compiles the launcher to a native executable and runs it.
* pyinbin interprets the launcher and the repository's Python sources.

Native and pyinbin transcripts (exit status, stdout, and stderr) are compared
to the CPython transcript with a unified diff.  The JSON report retains the
full command results so compiler failures are useful compatibility findings,
not ambiguous test failures.

Running tests or installing a project executes code from the cloned
repository.  The command therefore requires ``--allow-untrusted-code`` unless
``--discover-only`` is selected.
"""

from __future__ import annotations

import argparse
import dataclasses
import difflib
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Sequence


DEFAULT_QUERY = "pytest in:readme language:Python archived:false"
DEFAULT_PYTEST_ARGS = "-q --color=no --tb=short"
SCOUT_USER_AGENT = "asmpython-pytest-scout/1"
SKIP_SCAN_DIRS = {
    ".git", ".hg", ".svn", ".tox", ".nox", ".venv", "venv", "env",
    "node_modules", "build", "dist", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache",
}
PYTEST_CONFIG_FILES = ("pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini")


@dataclasses.dataclass(frozen=True)
class CommandResult:
    """Captured result of one subprocess, including timeouts."""

    args: tuple[str, ...]
    cwd: str
    returncode: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return not self.timed_out and self.returncode == 0


@dataclasses.dataclass(frozen=True)
class RepositoryCandidate:
    name: str
    clone_url: str | None
    ref: str | None = None
    local_path: str | None = None


@dataclasses.dataclass(frozen=True)
class CheckedOutRepository:
    candidate: RepositoryCandidate
    path: str
    commit: str
    clone_result: CommandResult | None = None


@dataclasses.dataclass(frozen=True)
class PytestEvidence:
    config_files: tuple[str, ...]
    conftest_files: tuple[str, ...]
    importing_tests: tuple[str, ...]
    test_files: tuple[str, ...]
    dependency_files: tuple[str, ...]
    scan_truncated: bool = False

    @property
    def is_pytest_repository(self) -> bool:
        explicit = self.config_files or self.conftest_files or self.importing_tests
        dependency = self.dependency_files and self.test_files
        return bool(explicit or dependency)


@dataclasses.dataclass(frozen=True)
class EnvironmentResult:
    ok: bool
    python: str
    pytest_version: str = ""
    import_roots: tuple[str, ...] = ()
    commands: tuple[CommandResult, ...] = ()
    warnings: tuple[str, ...] = ()
    detail: str = ""


@dataclasses.dataclass(frozen=True)
class ComparisonResult:
    status: str
    run: CommandResult | None = None
    compile: CommandResult | None = None
    diff: str = ""
    detail: str = ""


@dataclasses.dataclass(frozen=True)
class RepositoryResult:
    name: str
    source: str
    ref: str | None
    commit: str
    path: str
    evidence: PytestEvidence
    environment: EnvironmentResult | None
    baseline: CommandResult | None
    native: ComparisonResult
    pyinbin: ComparisonResult
    status: str
    detail: str = ""


def _run_command(
    args: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path,
    timeout: float,
    env: dict[str, str] | None = None,
) -> CommandResult:
    """Run a command without a shell and terminate its process group on timeout."""
    command = tuple(os.fspath(value) for value in args)
    started = time.monotonic()
    popen_kwargs: dict[str, Any] = {
        "cwd": str(cwd),
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = 0x00000200  # CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    try:
        process = subprocess.Popen(command, **popen_kwargs)
    except OSError as exc:
        return CommandResult(
            command, str(cwd), None, "", str(exc), time.monotonic() - started,
        )

    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_process_tree(process)
        stdout, stderr = process.communicate()

    return CommandResult(
        args=command,
        cwd=str(cwd),
        returncode=process.returncode,
        stdout=stdout or "",
        stderr=stderr or "",
        duration_seconds=time.monotonic() - started,
        timed_out=timed_out,
    )


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
        if process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            process.kill()


def _command_text(result: CommandResult) -> str:
    try:
        return shlex.join(result.args)
    except AttributeError:  # pragma: no cover - Python >=3.11 always has it
        return " ".join(shlex.quote(part) for part in result.args)


def parse_repository_spec(spec: str) -> RepositoryCandidate:
    """Parse a local path, ``owner/repo[@ref]``, or GitHub clone URL."""
    possible_path = Path(spec).expanduser()
    if possible_path.exists():
        resolved = possible_path.resolve()
        return RepositoryCandidate(
            name=resolved.name,
            clone_url=None,
            local_path=str(resolved),
        )

    if spec.startswith(("https://", "http://", "ssh://", "git@")):
        cleaned = spec.rstrip("/")
        tail = cleaned.rsplit("/", 1)[-1]
        name = tail.removesuffix(".git") or "repository"
        return RepositoryCandidate(name=name, clone_url=spec)

    ref: str | None = None
    repository = spec
    if "@" in spec:
        repository, ref = spec.rsplit("@", 1)
    parts = repository.strip("/").split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError(
            f"invalid repository {spec!r}; use owner/name[@ref], a GitHub URL, or a local path"
        )
    owner, name = parts
    return RepositoryCandidate(
        name=f"{owner}/{name}",
        clone_url=f"https://github.com/{owner}/{name}.git",
        ref=ref or None,
    )


def search_github_repositories(
    query: str,
    *,
    limit: int,
    token: str | None,
    timeout: float,
) -> list[RepositoryCandidate]:
    """Search GitHub's repository API; explicit repos remain available offline."""
    results: list[RepositoryCandidate] = []
    page = 1
    while len(results) < limit:
        per_page = min(100, limit - len(results))
        params = urllib.parse.urlencode(
            {
                "q": query,
                "sort": "updated",
                "order": "desc",
                "per_page": per_page,
                "page": page,
            }
        )
        request = urllib.request.Request(
            f"https://api.github.com/search/repositories?{params}",
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": SCOUT_USER_AGENT,
            },
        )
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GitHub search failed with HTTP {exc.code}: {body}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"GitHub search failed: {exc}") from exc

        items = payload.get("items", [])
        if not items:
            break
        for item in items:
            full_name = str(item.get("full_name", "")).strip()
            clone_url = str(item.get("clone_url", "")).strip()
            if full_name and clone_url:
                results.append(
                    RepositoryCandidate(
                        name=full_name,
                        clone_url=clone_url,
                        ref=str(item.get("default_branch") or "") or None,
                    )
                )
                if len(results) >= limit:
                    break
        page += 1
    return results


def _candidate_slug(candidate: RepositoryCandidate) -> str:
    locator = candidate.local_path or candidate.clone_url or candidate.name
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", candidate.name).strip("-.") or "repo"
    digest = hashlib.sha256(locator.encode("utf-8")).hexdigest()[:10]
    return f"{base}-{digest}"


def checkout_repository(
    candidate: RepositoryCandidate,
    *,
    clone_root: Path,
    refresh: bool,
    timeout: float,
) -> CheckedOutRepository:
    if candidate.local_path:
        path = Path(candidate.local_path).resolve()
        commit_result = _run_command(
            ["git", "rev-parse", "HEAD"], cwd=path, timeout=timeout,
        )
        commit = commit_result.stdout.strip() if commit_result.ok else "local-unversioned"
        return CheckedOutRepository(candidate, str(path), commit)

    clone_root.mkdir(parents=True, exist_ok=True)
    destination = clone_root / _candidate_slug(candidate)
    if refresh and destination.exists():
        shutil.rmtree(destination)
    if not destination.exists():
        command: list[str] = [
            "git", "clone", "--depth", "1", "--filter=blob:none", "--no-tags",
        ]
        if candidate.ref:
            command.extend(["--single-branch", "--branch", candidate.ref])
        command.extend(["--", candidate.clone_url or "", str(destination)])
        clone_result = _run_command(command, cwd=clone_root, timeout=timeout)
        if not clone_result.ok:
            raise RuntimeError(
                f"clone failed for {candidate.name}:\n"
                f"{clone_result.stderr or clone_result.stdout}"
            )
    else:
        clone_result = None

    commit_result = _run_command(
        ["git", "rev-parse", "HEAD"], cwd=destination, timeout=timeout,
    )
    if not commit_result.ok:
        raise RuntimeError(f"cannot resolve cloned commit for {candidate.name}")
    return CheckedOutRepository(
        candidate=candidate,
        path=str(destination.resolve()),
        commit=commit_result.stdout.strip(),
        clone_result=clone_result,
    )


def _read_probe(path: Path, limit: int = 262_144) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(limit)
    except OSError:
        return ""


def discover_pytest_evidence(root: Path, *, max_files: int = 10_000) -> PytestEvidence:
    """Find bounded, explicit evidence that a checkout uses pytest."""
    configs: list[str] = []
    dependencies: list[str] = []
    conftests: list[str] = []
    importing_tests: list[str] = []
    test_files: list[str] = []

    for name in PYTEST_CONFIG_FILES:
        path = root / name
        if not path.is_file():
            continue
        text = _read_probe(path).lower()
        if "pytest" in text or name == "pytest.ini":
            configs.append(name)

    for path in sorted(root.glob("requirements*.txt")):
        if "pytest" in _read_probe(path).lower():
            dependencies.append(path.relative_to(root).as_posix())

    scanned = 0
    truncated = False
    for directory, names, files in os.walk(root, followlinks=False):
        names[:] = sorted(name for name in names if name not in SKIP_SCAN_DIRS)
        base = Path(directory)
        for filename in sorted(files):
            scanned += 1
            if scanned > max_files:
                truncated = True
                break
            path = base / filename
            relative = path.relative_to(root).as_posix()
            if filename == "conftest.py":
                conftests.append(relative)
            is_test = (
                filename.startswith("test_") or filename.endswith("_test.py")
            ) and filename.endswith(".py")
            if is_test:
                test_files.append(relative)
                text = _read_probe(path)
                if re.search(r"(?m)^\s*(?:import\s+pytest\b|from\s+pytest\s+import\b)", text):
                    importing_tests.append(relative)
        if truncated:
            break

    return PytestEvidence(
        config_files=tuple(configs),
        conftest_files=tuple(conftests[:100]),
        importing_tests=tuple(importing_tests[:100]),
        test_files=tuple(test_files[:500]),
        dependency_files=tuple(dependencies),
        scan_truncated=truncated,
    )


def _venv_python(environment: Path) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def _project_is_installable(repository: Path) -> bool:
    return any((repository / name).is_file() for name in ("pyproject.toml", "setup.py", "setup.cfg"))


def prepare_environment(
    repository: Path,
    *,
    environment: Path,
    base_python: Path,
    pytest_requirement: str,
    project_install: str,
    requirements: Sequence[str],
    pip_installs: Sequence[str],
    timeout: float,
    refresh: bool,
) -> EnvironmentResult:
    commands: list[CommandResult] = []
    warnings: list[str] = []
    if refresh and environment.exists():
        shutil.rmtree(environment)
    python = _venv_python(environment)
    if not python.is_file():
        environment.parent.mkdir(parents=True, exist_ok=True)
        created = _run_command(
            [base_python, "-m", "venv", environment],
            cwd=repository,
            timeout=timeout,
        )
        commands.append(created)
        if not created.ok:
            return EnvironmentResult(
                False, str(python), commands=tuple(commands),
                detail=f"virtual environment creation failed: {created.stderr or created.stdout}",
            )

    pytest_install = _run_command(
        [python, "-m", "pip", "install", "--disable-pip-version-check", pytest_requirement],
        cwd=repository,
        timeout=timeout,
    )
    commands.append(pytest_install)
    if not pytest_install.ok:
        return EnvironmentResult(
            False, str(python), commands=tuple(commands),
            detail=f"pytest installation failed: {pytest_install.stderr or pytest_install.stdout}",
        )

    should_install_project = project_install == "editable" or (
        project_install == "auto" and _project_is_installable(repository)
    )
    if should_install_project:
        project_result = _run_command(
            [python, "-m", "pip", "install", "--disable-pip-version-check", "--editable", "."],
            cwd=repository,
            timeout=timeout,
        )
        commands.append(project_result)
        if not project_result.ok:
            detail = project_result.stderr or project_result.stdout
            if project_install == "editable":
                return EnvironmentResult(
                    False, str(python), commands=tuple(commands),
                    detail=f"editable project installation failed: {detail}",
                )
            warnings.append(f"automatic editable install failed; continuing with checkout imports: {detail.strip()}")

    for requirement in requirements:
        requirement_path = (repository / requirement).resolve()
        if not requirement_path.is_file():
            return EnvironmentResult(
                False, str(python), commands=tuple(commands),
                detail=f"requirements file not found: {requirement}",
            )
        result = _run_command(
            [python, "-m", "pip", "install", "--disable-pip-version-check", "-r", requirement_path],
            cwd=repository,
            timeout=timeout,
        )
        commands.append(result)
        if not result.ok:
            return EnvironmentResult(
                False, str(python), commands=tuple(commands), warnings=tuple(warnings),
                detail=f"dependency installation failed for {requirement}: {result.stderr or result.stdout}",
            )

    if pip_installs:
        result = _run_command(
            [python, "-m", "pip", "install", "--disable-pip-version-check", *pip_installs],
            cwd=repository,
            timeout=timeout,
        )
        commands.append(result)
        if not result.ok:
            return EnvironmentResult(
                False, str(python), commands=tuple(commands), warnings=tuple(warnings),
                detail=f"extra dependency installation failed: {result.stderr or result.stdout}",
            )

    probe_code = (
        "import json,sys,pytest; "
        "print(json.dumps({'pytest': pytest.__version__, 'path': sys.path}))"
    )
    probe = _run_command([python, "-c", probe_code], cwd=repository, timeout=timeout)
    commands.append(probe)
    if not probe.ok:
        return EnvironmentResult(
            False, str(python), commands=tuple(commands), warnings=tuple(warnings),
            detail=f"environment probe failed: {probe.stderr or probe.stdout}",
        )
    try:
        payload = json.loads(probe.stdout.strip().splitlines()[-1])
        roots = tuple(
            str(Path(item).resolve())
            for item in payload.get("path", [])
            if item and Path(item).is_dir()
        )
        version = str(payload.get("pytest", ""))
    except (json.JSONDecodeError, IndexError, TypeError) as exc:
        return EnvironmentResult(
            False, str(python), commands=tuple(commands), warnings=tuple(warnings),
            detail=f"invalid environment probe output: {exc}: {probe.stdout}",
        )
    return EnvironmentResult(
        True,
        str(python.resolve()),
        pytest_version=version,
        import_roots=roots,
        commands=tuple(commands),
        warnings=tuple(warnings),
    )


def _contains_python(directory: Path) -> bool:
    if (directory / "__init__.py").is_file():
        return True
    try:
        for child in directory.iterdir():
            if child.is_file() and child.suffix == ".py":
                return True
    except OSError:
        return False
    return False


def _overlay_entries(root: Path) -> Iterable[Path]:
    try:
        children = sorted(root.iterdir(), key=lambda path: path.name)
    except OSError:
        return ()
    selected: list[Path] = []
    for child in children:
        name = child.name
        if name.startswith(".") or name in SKIP_SCAN_DIRS:
            continue
        if child.is_file() and child.suffix == ".py" and child.stem.isidentifier():
            selected.append(child)
        elif child.is_dir() and name.isidentifier() and _contains_python(child):
            selected.append(child)
    return selected


def _copy_ignore(_directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        if name in SKIP_SCAN_DIRS or name.endswith((".pyc", ".pyo")):
            ignored.add(name)
    return ignored


def _place_overlay_entry(source: Path, destination: Path, *, force_copy: bool) -> None:
    if not force_copy:
        try:
            destination.symlink_to(source.resolve(), target_is_directory=source.is_dir())
            return
        except OSError:
            pass
    if source.is_dir():
        shutil.copytree(source, destination, symlinks=True, ignore=_copy_ignore)
    else:
        shutil.copy2(source, destination)


def build_import_overlay(
    destination: Path,
    roots: Sequence[Path],
    *,
    force_copy: bool = False,
) -> tuple[str, ...]:
    """Create one import root for native whole-program compilation."""
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    collisions: list[str] = []
    for root in roots:
        if not root.is_dir():
            continue
        for source in _overlay_entries(root):
            target = destination / source.name
            if target.exists() or target.is_symlink():
                collisions.append(f"{source.name}: kept earlier entry, skipped {source}")
                continue
            _place_overlay_entry(source, target, force_copy=force_copy)
    return tuple(collisions)


def render_pytest_launcher(pytest_args: Sequence[str]) -> str:
    """Render deliberately small source shared by all three execution modes."""
    return (
        "import pytest\n"
        f"_result = pytest.main({list(pytest_args)!r})\n"
        "if _result != 0:\n"
        "    raise SystemExit(_result)\n"
    )


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    output: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        key = os.path.normcase(str(resolved))
        if resolved.is_dir() and key not in seen:
            seen.add(key)
            output.append(resolved)
    return output


def _site_package_roots(import_roots: Sequence[str]) -> list[Path]:
    return _dedupe_paths(
        Path(root)
        for root in import_roots
        if Path(root).name in {"site-packages", "dist-packages"}
    )


def _test_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_ADDOPTS": "",
            "NO_COLOR": "1",
            "TERM": "dumb",
            "COLUMNS": "100",
        }
    )
    return environment


def _compiler_environment() -> dict[str, str]:
    environment = _test_environment()
    package_root = Path(__file__).resolve().parents[2]
    existing = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = str(package_root) + (os.pathsep + existing if existing else "")
    return environment


def _normalize_text(text: str, replacements: Sequence[tuple[str, str]]) -> str:
    value = text.replace("\r\n", "\n").replace("\r", "\n")
    for raw, label in sorted(replacements, key=lambda item: len(item[0]), reverse=True):
        if not raw:
            continue
        value = value.replace(raw, label)
        value = value.replace(raw.replace("\\", "/"), label)
    value = re.sub(r"\bat 0x[0-9a-fA-F]{8,16}\b", "at <address>", value)
    lines: list[str] = []
    for line in value.rstrip("\n").split("\n"):
        if re.search(
            r"\b(?:passed|failed|error|errors|skipped|deselected|warning|warnings)\b",
            line,
            re.IGNORECASE,
        ):
            line = re.sub(r"\bin \d+(?:\.\d+)?s\b", "in <time>s", line)
            line = re.sub(r"\b\d+(?:\.\d+)? seconds?\b", "<time> seconds", line)
        lines.append(line.rstrip())
    return "\n".join(lines)


def command_transcript(
    result: CommandResult,
    *,
    replacements: Sequence[tuple[str, str]] = (),
) -> str:
    code = "timeout" if result.timed_out else str(result.returncode)
    stdout = _normalize_text(result.stdout, replacements)
    stderr = _normalize_text(result.stderr, replacements)
    return f"exit: {code}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}"


def compare_commands(
    baseline: CommandResult,
    actual: CommandResult,
    *,
    actual_name: str,
    replacements: Sequence[tuple[str, str]],
) -> ComparisonResult:
    baseline_text = command_transcript(baseline, replacements=replacements)
    actual_text = command_transcript(actual, replacements=replacements)
    if baseline_text == actual_text:
        return ComparisonResult("match", run=actual)
    difference = "\n".join(
        difflib.unified_diff(
            baseline_text.splitlines(),
            actual_text.splitlines(),
            fromfile="cpython",
            tofile=actual_name,
            lineterm="",
        )
    )
    status = "timeout" if actual.timed_out else "diff"
    return ComparisonResult(status, run=actual, diff=difference)


def run_repository(
    checkout: CheckedOutRepository,
    evidence: PytestEvidence,
    environment: EnvironmentResult,
    *,
    work_root: Path,
    compiler_python: Path,
    pytest_args: Sequence[str],
    target: str,
    backend: str,
    linker: str | None,
    modes: set[str],
    run_timeout: float,
    compile_timeout: float,
    force_copy_overlay: bool,
) -> RepositoryResult:
    repository = Path(checkout.path)
    slug = _candidate_slug(checkout.candidate)
    run_root = work_root / "runs" / slug
    overlay = run_root / "overlay"
    output_root = run_root / "native"
    output_root.mkdir(parents=True, exist_ok=True)

    roots = [repository]
    source_root = repository / "src"
    if source_root.is_dir():
        roots.append(source_root)
    roots.extend(_site_package_roots(environment.import_roots))
    roots = _dedupe_paths(roots)
    collisions = build_import_overlay(overlay, roots, force_copy=force_copy_overlay)
    launcher = overlay / "__asmpython_pytest_scout__.py"
    launcher.write_text(render_pytest_launcher(pytest_args), encoding="utf-8")

    baseline = _run_command(
        [environment.python, launcher],
        cwd=repository,
        timeout=run_timeout,
        env=_test_environment(),
    )

    replacements = [
        (str(repository), "<repo>"),
        (str(work_root.resolve()), "<work>"),
        (str(Path(environment.python).parent.parent), "<venv>"),
        (str(Path(__file__).resolve().parents[2]), "<asmpython>"),
    ]
    native_result = ComparisonResult("skipped", detail="native mode not requested")
    if "native" in modes:
        executable = output_root / ("pytest-scout.exe" if target == "windows" else "pytest-scout")
        if executable.exists():
            executable.unlink()
        compile_command: list[str | os.PathLike[str]] = [
            compiler_python,
            "-m",
            "asmpython",
            "build",
            launcher,
            "--target",
            target,
            "--backend",
            backend,
            "--no-pyinbin-fallback",
            "-o",
            executable,
        ]
        if linker:
            compile_command.extend(["--linker", linker])
        compile_result = _run_command(
            compile_command,
            cwd=repository,
            timeout=compile_timeout,
            env=_compiler_environment(),
        )
        if not compile_result.ok or not executable.is_file():
            detail = compile_result.stderr or compile_result.stdout
            if compile_result.ok and not executable.is_file():
                detail = "compiler exited successfully but produced no native artifact"
            native_result = ComparisonResult(
                "compile-failed",
                compile=compile_result,
                detail=detail.strip(),
            )
        else:
            native_run = _run_command(
                [executable],
                cwd=repository,
                timeout=run_timeout,
                env=_test_environment(),
            )
            compared = compare_commands(
                baseline,
                native_run,
                actual_name="native",
                replacements=replacements,
            )
            native_result = dataclasses.replace(compared, compile=compile_result)

    pyinbin_result = ComparisonResult("skipped", detail="pyinbin mode not requested")
    if "pyinbin" in modes:
        pyinbin_command: list[str | os.PathLike[str]] = [
            compiler_python,
            "-m",
            "asmpython",
            "pyinbin",
            "run",
            launcher,
        ]
        pyinbin_roots = _dedupe_paths(
            [repository, source_root, *(Path(path) for path in environment.import_roots)]
        )
        for root in pyinbin_roots:
            pyinbin_command.extend(["--import-root", root])
        pyinbin_run = _run_command(
            pyinbin_command,
            cwd=repository,
            timeout=run_timeout,
            env=_compiler_environment(),
        )
        pyinbin_result = compare_commands(
            baseline,
            pyinbin_run,
            actual_name="pyinbin",
            replacements=replacements,
        )

    requested = [result for mode, result in (("native", native_result), ("pyinbin", pyinbin_result)) if mode in modes]
    status = "match" if baseline.ok and all(result.status == "match" for result in requested) else "failed"
    warning = "\n".join(environment.warnings)
    if collisions:
        collision_note = f"overlay kept the first source for {len(collisions)} import-name collision(s)"
        warning = f"{warning}\n{collision_note}".strip()
    return RepositoryResult(
        name=checkout.candidate.name,
        source=checkout.candidate.local_path or checkout.candidate.clone_url or "",
        ref=checkout.candidate.ref,
        commit=checkout.commit,
        path=str(repository),
        evidence=evidence,
        environment=environment,
        baseline=baseline,
        native=native_result,
        pyinbin=pyinbin_result,
        status=status,
        detail=warning,
    )


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {field.name: _jsonable(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _limited_lines(value: str, maximum: int) -> str:
    lines = value.splitlines()
    if len(lines) <= maximum:
        return value
    omitted = len(lines) - maximum
    return "\n".join([*lines[:maximum], f"... {omitted} additional line(s) in JSON report"])


def print_repository_result(result: RepositoryResult, *, max_diff_lines: int) -> None:
    baseline_state = "TIMEOUT" if result.baseline and result.baseline.timed_out else (
        f"exit {result.baseline.returncode}" if result.baseline else "not run"
    )
    print(f"\n{result.name} @ {result.commit[:12]}")
    print(f"  pytest evidence: {len(result.evidence.test_files)} test file(s)")
    print(f"  CPython: {baseline_state}")
    for label, comparison in (("native", result.native), ("pyinbin", result.pyinbin)):
        print(f"  {label}: {comparison.status.upper()}")
        if comparison.status == "compile-failed" and comparison.compile is not None:
            detail = comparison.detail or comparison.compile.stderr or comparison.compile.stdout
            for line in _limited_lines(detail.strip(), max_diff_lines).splitlines():
                print(f"      {line}")
        elif comparison.diff:
            for line in _limited_lines(comparison.diff, max_diff_lines).splitlines():
                print(f"      {line}")
    if result.detail:
        for line in _limited_lines(result.detail, max_diff_lines).splitlines():
            print(f"  note: {line}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_argument_group("repository discovery")
    source.add_argument(
        "--repo",
        action="append",
        default=[],
        metavar="OWNER/NAME[@REF]|PATH",
        help="explicit repository (repeatable); when omitted, search GitHub",
    )
    source.add_argument(
        "--query",
        default=None,
        help=f"GitHub repository search query (default: {DEFAULT_QUERY!r})",
    )
    source.add_argument("--limit", type=int, default=5, help="maximum verified pytest repositories")
    source.add_argument("--github-token-env", default="GITHUB_TOKEN")
    source.add_argument(
        "--discover-only",
        action="store_true",
        help="clone and identify pytest repos without executing them",
    )

    execution = parser.add_argument_group("execution")
    execution.add_argument(
        "--allow-untrusted-code",
        action="store_true",
        help="required acknowledgement that installs and tests execute repository code",
    )
    execution.add_argument("--workspace", type=Path, default=Path(".asmpython-pytest-scout"))
    execution.add_argument(
        "--refresh",
        action="store_true",
        help="re-clone repositories and recreate virtual environments",
    )
    execution.add_argument(
        "--python",
        type=Path,
        default=Path(sys.executable),
        help="Python used to create baseline virtual environments",
    )
    execution.add_argument(
        "--compiler-python",
        type=Path,
        default=Path(sys.executable),
        help="Python hosting asmpython and pyinbin",
    )
    execution.add_argument("--pytest", default="pytest", dest="pytest_requirement", help="pip requirement for pytest")
    execution.add_argument(
        "--project-install",
        choices=("auto", "editable", "none"),
        default="auto",
        help="whether to pip-install each cloned project (auto continues if editable install fails)",
    )
    execution.add_argument(
        "--requirements",
        action="append",
        default=[],
        metavar="FILE",
        help="requirements file relative to each repo",
    )
    execution.add_argument(
        "--pip-install",
        action="append",
        default=[],
        metavar="SPEC",
        help="additional package to install (repeatable)",
    )
    execution.add_argument(
        "--pytest-args",
        default=DEFAULT_PYTEST_ARGS,
        help="arguments passed to pytest in every execution mode",
    )
    execution.add_argument("--mode", choices=("all", "native", "pyinbin"), default="all")
    execution.add_argument("--target", choices=("linux", "windows"), default="windows" if os.name == "nt" else "linux")
    execution.add_argument("--backend", choices=("legacy", "x86-64"), default="legacy")
    execution.add_argument("--linker", choices=("gcc", "builtin"), default=None)
    execution.add_argument("--timeout", type=float, default=300.0, help="seconds allowed for each test run")
    execution.add_argument("--compile-timeout", type=float, default=600.0)
    execution.add_argument("--setup-timeout", type=float, default=600.0)
    execution.add_argument("--clone-timeout", type=float, default=180.0)
    execution.add_argument(
        "--copy-overlay",
        action="store_true",
        help="copy import sources instead of preferring symlinks",
    )

    output = parser.add_argument_group("reporting")
    output.add_argument("--report", type=Path, default=None, help="JSON report path (default: <workspace>/report.json)")
    output.add_argument("--max-diff-lines", type=int, default=120)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit must be at least 1")
    for name in ("timeout", "compile_timeout", "setup_timeout", "clone_timeout"):
        if getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be greater than zero")
    if not args.discover_only and not args.allow_untrusted_code:
        parser.error(
            "repository installation and tests execute untrusted code; pass "
            "--allow-untrusted-code or use --discover-only"
        )

    workspace = args.workspace.expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    report_path = (args.report or (workspace / "report.json")).expanduser().resolve()

    candidates: list[RepositoryCandidate] = []
    try:
        candidates.extend(parse_repository_spec(spec) for spec in args.repo)
    except ValueError as exc:
        parser.error(str(exc))
    if args.query or not candidates:
        query = args.query or DEFAULT_QUERY
        token = os.environ.get(args.github_token_env) if args.github_token_env else None
        try:
            searched = search_github_repositories(
                query,
                limit=max(args.limit * 3, args.limit),
                token=token,
                timeout=args.clone_timeout,
            )
        except RuntimeError as exc:
            print(f"pytest scout: {exc}", file=sys.stderr)
            return 2
        candidates.extend(searched)

    unique: list[RepositoryCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = candidate.local_path or candidate.clone_url or candidate.name
        if key not in seen:
            seen.add(key)
            unique.append(candidate)

    modes = {"native", "pyinbin"} if args.mode == "all" else {args.mode}
    pytest_args = shlex.split(args.pytest_args, posix=os.name != "nt")
    results: list[RepositoryResult] = []
    discovery: list[dict[str, Any]] = []
    verified = 0

    for candidate in unique:
        if verified >= args.limit:
            break
        print(f"checking {candidate.name} ...", flush=True)
        try:
            checkout = checkout_repository(
                candidate,
                clone_root=workspace / "clones",
                refresh=args.refresh,
                timeout=args.clone_timeout,
            )
        except RuntimeError as exc:
            discovery.append({"name": candidate.name, "status": "clone-failed", "detail": str(exc)})
            print(f"  clone failed: {exc}", file=sys.stderr)
            continue
        repository = Path(checkout.path)
        evidence = discover_pytest_evidence(repository)
        discovery.append(
            {
                "name": candidate.name,
                "path": str(repository),
                "commit": checkout.commit,
                "pytest": evidence.is_pytest_repository,
                "evidence": _jsonable(evidence),
            }
        )
        if not evidence.is_pytest_repository:
            print("  skipped: no explicit pytest evidence")
            continue
        verified += 1
        print(f"  found {len(evidence.test_files)} test file(s)")
        if args.discover_only:
            continue

        environment = prepare_environment(
            repository,
            environment=workspace / "venvs" / _candidate_slug(candidate),
            base_python=args.python.expanduser().resolve(),
            pytest_requirement=args.pytest_requirement,
            project_install=args.project_install,
            requirements=args.requirements,
            pip_installs=args.pip_install,
            timeout=args.setup_timeout,
            refresh=args.refresh,
        )
        if not environment.ok:
            result = RepositoryResult(
                name=candidate.name,
                source=candidate.local_path or candidate.clone_url or "",
                ref=candidate.ref,
                commit=checkout.commit,
                path=str(repository),
                evidence=evidence,
                environment=environment,
                baseline=None,
                native=ComparisonResult("skipped", detail="environment setup failed"),
                pyinbin=ComparisonResult("skipped", detail="environment setup failed"),
                status="failed",
                detail=environment.detail,
            )
        else:
            result = run_repository(
                checkout,
                evidence,
                environment,
                work_root=workspace,
                compiler_python=args.compiler_python.expanduser().resolve(),
                pytest_args=pytest_args,
                target=args.target,
                backend=args.backend,
                linker=args.linker,
                modes=modes,
                run_timeout=args.timeout,
                compile_timeout=args.compile_timeout,
                force_copy_overlay=args.copy_overlay,
            )
        results.append(result)
        print_repository_result(result, max_diff_lines=args.max_diff_lines)

    report = {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "query": args.query or (None if args.repo else DEFAULT_QUERY),
        "pytest_args": pytest_args,
        "modes": sorted(modes),
        "discovery": discovery,
        "repositories": _jsonable(results),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"\nreport: {report_path}")

    if args.discover_only:
        print(f"verified pytest repositories: {verified}")
        return 0 if verified else 1
    passed = sum(result.status == "match" for result in results)
    print(f"matched: {passed}/{len(results)} repository run(s)")
    return 0 if results and passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
