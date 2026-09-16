"""`python -m uasm` -- the development entry point.

Deliberately NOT a script named `uasm.py` beside `src/`: a top-level module
with the package's own name shadows the package on `sys.path`, and the failure
is baffling ("No module named 'uasm.driver'; 'uasm' is not a package") because it
names the package that clearly does exist. pytest adds the rootdir to the path,
so the whole suite failed to collect.

Installed users get the `uasm` console script from pyproject.toml.
"""
from .driver.cli import main

raise SystemExit(main())
