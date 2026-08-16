# CPython Conformance Gate

`tests/cpython_conformance.py` is the release-gate harness for the official
CPython `Lib/test` suite. It runs the selected module set through CPython as a
baseline and, independently, through `asmpython pyinbin run` with the same
stdlib root available to the explicit source loader.

Run a quick smoke check:

```text
python -m tests.cpython_conformance --limit 10 --mode both
```

Run the complete release gate:

```text
python -m tests.cpython_conformance --mode both --required --jobs 4
```

`--required` makes any pyinbin failure fail the command. A green workspace
suite is not sufficient for 3.14 readiness: the full official-suite command,
its interpreter version, and its pass/fail counts must be recorded in the
release checklist before the version is called ready.
