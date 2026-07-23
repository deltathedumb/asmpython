"""Deprecated alias of :mod:`asmpython.annotations`.

``asmpython.extras`` was renamed to ``asmpython.annotations``. This package
re-exports the same objects (not copies) for compatibility; prefer
``from asmpython import Public, access`` or ``asmpython.annotations`` directly
in new source.
"""
from ..annotations import *
from ..annotations import __all__
