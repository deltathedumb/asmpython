from ._compiler.__main__ import main

try:
    from ._backends.host_cli import main as _host_main
except ImportError:
    _host_main = None


if __name__ == "__main__":
    # CPython can load the host-only policy layer.  A self-hosted native build
    # intentionally excludes asmpython/_backends from whole-program merging, so
    # the unresolved host binding stays false and the statically merged legacy
    # compiler entry point remains available.
    if _host_main:
        raise SystemExit(_host_main())
    raise SystemExit(main())
