"""Threading declarations; prefer importing these from :mod:`asmpython`."""
from ._api import atomic, threadlocal, sync, threadsafe, mainonly

__all__ = ["atomic", "threadlocal", "sync", "threadsafe", "mainonly"]
