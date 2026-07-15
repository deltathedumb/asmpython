# Publishing to PyPI

PyPI distributions are released only from the dedicated `pypi` branch. The
development branches (`beta` and `main`) are never valid publication sources.

## One version source

`asmpython/__init__.py` is the only version source:

```python
__version__ = "1.2.0"
```

`pyproject.toml` reads that value through setuptools' dynamic metadata. Do not
add a second literal `project.version`; the old publishing flow did that and
the two values diverged, causing the 1.2.0 GitHub release to build the already
used 1.1.0 PyPI version.

## Release process

1. Bring only the release-ready source into `pypi` and update
   `asmpython.__version__` to a version that does not already exist on PyPI.
2. Run the workspace tests and build both artifacts:

   ```text
   python -m pip install --upgrade build packaging twine
   pyproject-build --outdir dist .
   python -m twine check --strict dist/*
   python scripts/verify_release_artifacts.py --tag v1.2.1 --project asmpython dist/*
   ```

   Use `pyproject-build`, not `python -m build`: this repository's top-level
   `build.py` is the self-host compiler driver and intentionally occupies that
   import name.

3. Commit the release on `pypi` and push it.
4. Tag that exact branch tip with the same v-prefixed version and push the tag:

   ```text
   git switch pypi
   git tag v1.2.1
   git push origin pypi v1.2.1
   ```

5. Publish the GitHub release for that existing tag. The workflow builds the
   tagged source and publishes through PyPI Trusted Publishing.

The workflow rejects a tag unless its commit is exactly the current remote
`pypi` tip. It then rejects the artifacts unless the tag, wheel metadata, and
sdist metadata normalize to the same version. This prevents a beta/main tag,
a stale branch, or mismatched package metadata from reaching PyPI.

For recovery after a transient workflow failure, run the workflow manually
and provide the same existing tag. Do not move a published tag or reuse a PyPI
version; both release tags and uploaded distribution files are immutable.

## Trusted Publisher configuration

The `asmpython` project on PyPI must trust this exact GitHub identity:

- Owner: `deltathedumb`
- Repository: `asmpython`
- Workflow: `publish.yml`
- Environment: `pypi`

The publish job requests only `id-token: write` and uses the `pypi` GitHub
environment. No long-lived PyPI API token is stored in GitHub.
