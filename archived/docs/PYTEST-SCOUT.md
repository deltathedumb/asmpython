# Pytest Repository Scout

`asmpython-pytest-scout` measures asmpython and pyinbin against real pytest
projects. It searches for or accepts repositories, clones them, builds an
isolated test environment, and runs one generated pytest launcher three ways:

1. CPython is the interpreted baseline.
2. asmpython compiles the launcher and runs the native artifact.
3. pyinbin interprets the launcher and repository source.

The native and pyinbin exit status, stdout, and stderr are independently
compared with the CPython baseline. Every mismatch is printed as a unified diff
and retained in a machine-readable JSON report.

## Safety

Cloned projects are untrusted. Installing an editable package and running its
tests can execute arbitrary code with the current user's permissions. The
scout refuses to execute until `--allow-untrusted-code` is supplied. Use
`--discover-only` to clone and inspect pytest evidence without installing or
running repository code.

The per-repository virtual environment is dependency isolation, not a security
sandbox. Run broad public-repository sweeps in a disposable VM or container.

## Quick start

Test one repository and every engine:

```text
asmpython-pytest-scout \
  --repo pallets/itsdangerous \
  --allow-untrusted-code
```

Search GitHub and process the first five verified pytest repositories:

```text
asmpython-pytest-scout \
  --query "pytest in:readme language:Python stars:50..500 archived:false" \
  --limit 5 \
  --allow-untrusted-code
```

GitHub's unauthenticated search limit is small. Set `GITHUB_TOKEN` to use an
authenticated API request, or change the variable name with
`--github-token-env`.

Run only the pyinbin comparison with a narrow pytest selection:

```text
asmpython-pytest-scout \
  --repo owner/project@main \
  --mode pyinbin \
  --pytest-args "-q tests/test_core.py -x --color=no" \
  --allow-untrusted-code
```

## Repository setup

Each repository gets a reusable virtual environment under
`.asmpython-pytest-scout/venvs/`. The default `--project-install auto` attempts
an editable install when packaging metadata exists, but records a warning and
continues from the checkout if that install fails. Use:

- `--project-install editable` to make an install failure fatal;
- `--project-install none` to test directly from the checkout;
- `--requirements requirements-test.txt` for an additional requirements file;
- `--pip-install package-or-extra` for extra test dependencies;
- `--refresh` to replace cached clones and virtual environments.

The scout builds a temporary union import root from the checkout, a `src/`
directory when present, and the environment's site-packages. Symlinks are used
when possible; `--copy-overlay` forces copies for platforms where symlink
creation is unavailable.

## Native versus pyinbin separation

Normal `asmpython build` may execute a rejected source through pyinbin. That is
useful for users, but would make a native conformance result ambiguous. The
scout compiles with `--no-pyinbin-fallback`, so a native compiler rejection is
reported as `COMPILE-FAILED` and pyinbin is measured separately.

Pytest dynamically discovers and imports test modules. Native failures around
that behavior are valid compatibility findings: the scout never treats a
pyinbin fallback execution as a native artifact. Pyinbin receives the
repository, environment, and standard-library import roots so its dynamic
loader can run the same pytest launcher.

## Results

The terminal output uses these states:

- `MATCH`: normalized exit status, stdout, and stderr equal CPython.
- `DIFF`: execution completed but the transcript differs.
- `COMPILE-FAILED`: no native artifact was produced.
- `TIMEOUT`: the engine exceeded its per-run deadline.
- `SKIPPED`: that engine was not requested or setup failed first.

Volatile work paths, object addresses, line endings, and elapsed-time strings
are normalized before comparison. The original output remains in `report.json`
along with commands, durations, commits, pytest evidence, setup operations,
and the complete diffs.

The command exits zero only when the CPython baseline passes and every
requested engine matches it for every processed repository.

