from ._compiler.__main__ import main as _legacy_main

try:
    from ._compiler.cli import main as _host_main
except ImportError:
    _host_main = None


if __name__ == "__main__":
    # CPython can load the host management/policy layer. A self-hosted native
    # build intentionally excludes host-only modules, so it retains the static
    # legacy compiler entry point.
    if _host_main:
        raise SystemExit(_host_main())
    raise SystemExit(_legacy_main())
