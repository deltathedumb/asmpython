"""shelve: persistent dictionary backed by a plain-text file using pickle.

Usage::

    import shelve

    with shelve.open("mydata") as db:
        db["name"] = "Alice"        # str values via __setitem__
        db.set_int("age", 30)       # typed setter
        db.set_list("scores", [10, 20, 30])
        db.set_dict("prefs", {"color": "blue"})

    with shelve.open("mydata") as db:
        name = db["name"]                    # -> "Alice"
        age  = db.get_int("age")             # -> 30
        scores = db.get_list("scores")       # -> [10, 20, 30]
        prefs  = db.get_dict("prefs")        # -> {"color": "blue"}

Internally each value is stored as its pickle string representation, and the
entire shelf is persisted as a pickle dict mapping str keys -> pickled-value
strings.  The format is our custom text-based pickle (not CPython-compatible).

Typed set/get methods:
  set_str / get_str    — str values
  set_int / get_int    — int values
  set_float / get_float — float values
  set_list / get_list  — list values
  set_dict / get_dict  — dict values (flat string-keyed, no nested containers)
"""
from __future__ import annotations

import os
import pickle as _pickle


def _read_file(filename: str) -> str:
    f = open(filename, "r")
    content: str = f.read()
    f.close()
    return content


def _write_file(filename: str, content: str) -> None:
    f = open(filename, "w")
    f.write(content)
    f.close()


class Shelf:
    """Persistent dictionary. Keys are str; values are stored via pickle."""

    def __init__(self, filename: str, flag: str = "c") -> None:
        self._filename: str = filename
        self._flag: str = flag
        # _pickled: key -> pickled-value string (e.g. "i42", 's"hello"', "[i1,i2]")
        self._pickled: dict = {}
        self._modified: int = 0
        if flag != "n" and os.path.exists(filename):
            self._load()

    def _load(self) -> None:
        content: str = _read_file(self._filename)
        if not content:
            return
        # Outer format: pickle dict {s"key":s"pickled_val",...}
        loaded: dict = _pickle.loads_dict(content)
        for k in loaded:
            self._pickled[k] = loaded[k]

    def _save(self) -> None:
        if self._flag == "r":
            return
        # Persist as pickle dict: key -> pickled-value string
        content: str = _pickle.dumps_dict(self._pickled)
        _write_file(self._filename, content)
        self._modified = 0

    # ------------------------------------------------------------------
    # Mapping protocol
    # ------------------------------------------------------------------

    def __contains__(self, key: str) -> int:
        return key in self._pickled

    def __getitem__(self, key: str) -> str:
        """Get a str value (key must have been set with __setitem__ or set_str)."""
        return _pickle.loads_str(self._pickled[key])

    def __setitem__(self, key: str, value: str) -> None:
        """Store a str value."""
        self._pickled[key] = _pickle.dumps_str(value)
        self._modified = 1

    def __delitem__(self, key: str) -> None:
        del self._pickled[key]
        self._modified = 1

    def __len__(self) -> int:
        return len(self._pickled)

    # ------------------------------------------------------------------
    # Typed str accessors (generic str path)
    # ------------------------------------------------------------------

    def get(self, key: str, default: str = "") -> str:
        if key in self._pickled:
            return _pickle.loads_str(self._pickled[key])
        return default

    def set_str(self, key: str, value: str) -> None:
        self._pickled[key] = _pickle.dumps_str(value)
        self._modified = 1

    def get_str(self, key: str, default: str = "") -> str:
        if key in self._pickled:
            return _pickle.loads_str(self._pickled[key])
        return default

    # ------------------------------------------------------------------
    # Typed int accessors
    # ------------------------------------------------------------------

    def set_int(self, key: str, value: int) -> None:
        self._pickled[key] = _pickle.dumps_int(value)
        self._modified = 1

    def get_int(self, key: str, default: int = 0) -> int:
        if key in self._pickled:
            return _pickle.loads_int(self._pickled[key])
        return default

    # ------------------------------------------------------------------
    # Typed float accessors
    # ------------------------------------------------------------------

    def set_float(self, key: str, value: float) -> None:
        self._pickled[key] = _pickle.dumps_float(value)
        self._modified = 1

    def get_float(self, key: str, default: float = 0.0) -> float:
        if key in self._pickled:
            return _pickle.loads_float(self._pickled[key])
        return default

    # ------------------------------------------------------------------
    # Typed list accessors
    # ------------------------------------------------------------------

    def set_list(self, key: str, value: list) -> None:
        self._pickled[key] = _pickle.dumps_list(value)
        self._modified = 1

    def get_list(self, key: str) -> list:
        if key in self._pickled:
            return _pickle.loads_list(self._pickled[key])
        return []

    # ------------------------------------------------------------------
    # Typed dict accessors (flat string-keyed dicts only)
    # ------------------------------------------------------------------

    def set_dict(self, key: str, value: dict) -> None:
        self._pickled[key] = _pickle.dumps_dict(value)
        self._modified = 1

    def get_dict(self, key: str) -> dict:
        if key in self._pickled:
            return _pickle.loads_dict(self._pickled[key])
        return {}

    # ------------------------------------------------------------------
    # Key/value iteration
    # ------------------------------------------------------------------

    def keys(self) -> list:
        result: list = []
        for k in self._pickled:
            result.append(k)
        return result

    def values(self) -> list:
        result: list = []
        for k in self._pickled:
            result.append(_pickle.loads_str(self._pickled[k]))
        return result

    def items(self) -> list:
        result: list = []
        for k in self._pickled:
            pair: list = [k, _pickle.loads_str(self._pickled[k])]
            result.append(pair)
        return result

    def pop(self, key: str, default: str = "") -> str:
        if key in self._pickled:
            val: str = _pickle.loads_str(self._pickled[key])
            del self._pickled[key]
            self._modified = 1
            return val
        return default

    def setdefault(self, key: str, default: str = "") -> str:
        if key not in self._pickled:
            self._pickled[key] = _pickle.dumps_str(default)
            self._modified = 1
        return _pickle.loads_str(self._pickled[key])

    def update(self, other: dict) -> None:
        for k in other:
            self._pickled[k] = _pickle.dumps_str(other[k])
        self._modified = 1

    def clear(self) -> None:
        self._pickled = {}
        self._modified = 1

    # ------------------------------------------------------------------
    # Sync / close / context manager
    # ------------------------------------------------------------------

    def sync(self) -> None:
        """Flush pending changes to disk."""
        if self._modified:
            self._save()

    def close(self) -> None:
        """Sync and release the shelf."""
        self.sync()

    def __enter__(self) -> Shelf:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> int:
        self.close()
        return 0

    def __repr__(self) -> str:
        return "shelve.Shelf(" + self._filename + ")"


def open(filename: str, flag: str = "c", writeback: int = 0) -> Shelf:
    """Open a persistent dictionary stored in *filename*.

    flag:
      'c'  create or open existing (default)
      'r'  read-only
      'w'  open existing for writing
      'n'  always create a new empty shelf
    """
    return Shelf(filename, flag)
