"""Generate probes for stdlib modules that need a filesystem, a clock or a
subprocess -- and establish the fixture convention that makes them possible.

37 of the 110 modules in `asmpython/stdlib/` were unprobed after the first
expansion, and almost all for the same reason: they do not do anything
observable without external state. `shutil` needs files to copy, `glob` needs
a directory to match against, `sqlite3` needs a database, `subprocess` needs a
process, `time` needs a clock. A single-file corpus case had no way to supply
any of that, so the whole group stayed dark.

It does not actually need new runner machinery. It needs three rules:

1. **The case builds its own fixture and removes it.** A directory under
   `tempfile.gettempdir()`, a fixed name unique to that case, `try/finally`
   cleanup. No shared state between cases, so parallel workers cannot collide.

2. **Never print anything environment-derived.** A temp path, a file mode, a
   timestamp and a process id are all different on every run and on every
   machine; printing one pins the corpus to this host. Print what was
   *derived*: the bytes read back, the count of entries, whether a delta was
   non-negative.

3. **Clocks are asserted as deltas or orderings, never as values.** This is
   the round-1 caveat applied up front: the double-run determinism check
   catches per-process randomness, not timing races, so a probe must not be
   able to fail because a machine was slow. `t1 >= t0` is a real assertion
   about `monotonic`; `t1 - t0 < 5.0` is a flake waiting to happen.

`fix_tempdir_write_read` deliberately owns the shared prerequisite -- creating
a file under the temp directory and reading it back. If it fails, every other
probe here fails for that one reason rather than for its own, and the report
should say so instead of counting 30 independent findings.

Usage: python gen_fixture_cases.py <tests/cases dir>
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _emit import CaseSet, main  # noqa: E402

CASES = CaseSet("probes")
case = CASES.case


# ---------------------------------------------------------------------------
# the shared prerequisite, isolated in one probe
# ---------------------------------------------------------------------------

case("fix_tempdir_write_read", "a file can be written under the temp dir and read back", r'''
import os
import tempfile

path = os.path.join(tempfile.gettempdir(), "asmpy_fix_basic.txt")
try:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("payload")
    with open(path, "r", encoding="utf-8") as handle:
        print(handle.read())
finally:
    if os.path.exists(path):
        os.remove(path)
''')


# ---------------------------------------------------------------------------
# builtin file I/O
# ---------------------------------------------------------------------------

case("fix_open_append_mode", "append mode adds to an existing file", r'''
import os
import tempfile

path = os.path.join(tempfile.gettempdir(), "asmpy_fix_append.txt")
try:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("one\n")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("two\n")
    with open(path, "r", encoding="utf-8") as handle:
        print(handle.read(), end="")
finally:
    if os.path.exists(path):
        os.remove(path)
''')

case("fix_open_readlines", "readlines splits a file into lines", r'''
import os
import tempfile

path = os.path.join(tempfile.gettempdir(), "asmpy_fix_lines.txt")
try:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("a\nb\nc\n")
    with open(path, "r", encoding="utf-8") as handle:
        print(handle.readlines())
finally:
    if os.path.exists(path):
        os.remove(path)
''')

case("fix_open_iterates_lines", "a file object iterates line by line", r'''
import os
import tempfile

path = os.path.join(tempfile.gettempdir(), "asmpy_fix_iter.txt")
try:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("first\nsecond\n")
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            print(line.rstrip())
finally:
    if os.path.exists(path):
        os.remove(path)
''')

case("fix_open_binary_roundtrip", "binary mode round-trips exact bytes", r'''
import os
import tempfile

path = os.path.join(tempfile.gettempdir(), "asmpy_fix_bin.dat")
payload = b"\x00\x01\xfe\xff"
try:
    with open(path, "wb") as handle:
        handle.write(payload)
    with open(path, "rb") as handle:
        read_back = handle.read()
    print(len(read_back))
    print(read_back == payload)
finally:
    if os.path.exists(path):
        os.remove(path)
''')

case("fix_open_missing_raises", "opening a missing file raises FileNotFoundError", r'''
import os
import tempfile

path = os.path.join(tempfile.gettempdir(), "asmpy_fix_absent.txt")
if os.path.exists(path):
    os.remove(path)
try:
    open(path, "r", encoding="utf-8")
    print("opened")
except FileNotFoundError:
    print("refused")
''')


# ---------------------------------------------------------------------------
# os / os.path against real files
# ---------------------------------------------------------------------------

case("fix_os_path_exists_tracks_file", "os.path.exists follows create and remove", r'''
import os
import tempfile

path = os.path.join(tempfile.gettempdir(), "asmpy_fix_exists.txt")
try:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("x")
    print(os.path.exists(path))
    print(os.path.isfile(path))
    os.remove(path)
    print(os.path.exists(path))
finally:
    if os.path.exists(path):
        os.remove(path)
''')

case("fix_os_stat_size", "os.path.getsize reports the byte count", r'''
import os
import tempfile

path = os.path.join(tempfile.gettempdir(), "asmpy_fix_size.txt")
try:
    with open(path, "wb") as handle:
        handle.write(b"12345")
    print(os.path.getsize(path))
finally:
    if os.path.exists(path):
        os.remove(path)
''')

case("fix_os_listdir_sees_created_files", "os.listdir reports created entries", r'''
import os
import shutil
import tempfile

work = os.path.join(tempfile.gettempdir(), "asmpy_fix_listdir")
try:
    os.makedirs(work, exist_ok=True)
    for name in ["b.txt", "a.txt"]:
        with open(os.path.join(work, name), "w", encoding="utf-8") as handle:
            handle.write("x")
    print(sorted(os.listdir(work)))
finally:
    shutil.rmtree(work, ignore_errors=True)
''')

case("fix_os_makedirs_nested", "makedirs creates intermediate directories", r'''
import os
import shutil
import tempfile

root = os.path.join(tempfile.gettempdir(), "asmpy_fix_mkdirs")
deep = os.path.join(root, "a", "b")
try:
    os.makedirs(deep, exist_ok=True)
    print(os.path.isdir(deep))
finally:
    shutil.rmtree(root, ignore_errors=True)
''')

case("fix_os_rename_moves_file", "os.rename moves a file", r'''
import os
import tempfile

src = os.path.join(tempfile.gettempdir(), "asmpy_fix_rename_a.txt")
dst = os.path.join(tempfile.gettempdir(), "asmpy_fix_rename_b.txt")
try:
    with open(src, "w", encoding="utf-8") as handle:
        handle.write("moved")
    os.rename(src, dst)
    print(os.path.exists(src))
    with open(dst, "r", encoding="utf-8") as handle:
        print(handle.read())
finally:
    for path in (src, dst):
        if os.path.exists(path):
            os.remove(path)
''')

case("fix_os_walk_finds_tree", "os.walk enumerates a directory tree", r'''
import os
import shutil
import tempfile

root = os.path.join(tempfile.gettempdir(), "asmpy_fix_walk")
try:
    os.makedirs(os.path.join(root, "sub"), exist_ok=True)
    with open(os.path.join(root, "top.txt"), "w", encoding="utf-8") as handle:
        handle.write("x")
    with open(os.path.join(root, "sub", "deep.txt"), "w", encoding="utf-8") as handle:
        handle.write("y")
    found = []
    for _, _, files in os.walk(root):
        found.extend(files)
    print(sorted(found))
finally:
    shutil.rmtree(root, ignore_errors=True)
''')


# ---------------------------------------------------------------------------
# modules that were blocked purely on having files to work with
# ---------------------------------------------------------------------------

case("fix_shutil_copy", "shutil.copy duplicates a file's contents", r'''
import os
import shutil
import tempfile

src = os.path.join(tempfile.gettempdir(), "asmpy_fix_copy_src.txt")
dst = os.path.join(tempfile.gettempdir(), "asmpy_fix_copy_dst.txt")
try:
    with open(src, "w", encoding="utf-8") as handle:
        handle.write("contents")
    shutil.copy(src, dst)
    with open(dst, "r", encoding="utf-8") as handle:
        print(handle.read())
finally:
    for path in (src, dst):
        if os.path.exists(path):
            os.remove(path)
''')

case("fix_shutil_rmtree_removes_tree", "shutil.rmtree removes a populated directory", r'''
import os
import shutil
import tempfile

root = os.path.join(tempfile.gettempdir(), "asmpy_fix_rmtree")
os.makedirs(root, exist_ok=True)
with open(os.path.join(root, "f.txt"), "w", encoding="utf-8") as handle:
    handle.write("x")
print(os.path.isdir(root))
shutil.rmtree(root)
print(os.path.isdir(root))
''')

case("fix_glob_matches_pattern", "glob matches files by pattern", r'''
import glob
import os
import shutil
import tempfile

root = os.path.join(tempfile.gettempdir(), "asmpy_fix_glob")
try:
    os.makedirs(root, exist_ok=True)
    for name in ["a.py", "b.txt", "c.py"]:
        with open(os.path.join(root, name), "w", encoding="utf-8") as handle:
            handle.write("x")
    hits = glob.glob(os.path.join(root, "*.py"))
    print(sorted(os.path.basename(p) for p in hits))
finally:
    shutil.rmtree(root, ignore_errors=True)
''')

case("fix_pathlib_write_read_text", "Path.write_text / read_text round-trip", r'''
import pathlib
import tempfile

path = pathlib.Path(tempfile.gettempdir()) / "asmpy_fix_pathlib.txt"
try:
    path.write_text("through pathlib", encoding="utf-8")
    print(path.read_text(encoding="utf-8"))
    print(path.exists())
finally:
    if path.exists():
        path.unlink()
''')

case("fix_pathlib_iterdir", "Path.iterdir lists a directory", r'''
import pathlib
import shutil
import tempfile

root = pathlib.Path(tempfile.gettempdir()) / "asmpy_fix_iterdir"
try:
    root.mkdir(exist_ok=True)
    (root / "one.txt").write_text("1", encoding="utf-8")
    (root / "two.txt").write_text("2", encoding="utf-8")
    print(sorted(p.name for p in root.iterdir()))
finally:
    shutil.rmtree(root, ignore_errors=True)
''')

case("fix_linecache_getline", "linecache reads a numbered line from a file", r'''
import linecache
import os
import tempfile

path = os.path.join(tempfile.gettempdir(), "asmpy_fix_linecache.txt")
try:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("alpha\nbeta\ngamma\n")
    print(linecache.getline(path, 2).rstrip())
    print(linecache.getline(path, 99) == "")
finally:
    linecache.clearcache()
    if os.path.exists(path):
        os.remove(path)
''')

case("fix_fileinput_reads_files", "fileinput concatenates several files", r'''
import fileinput
import os
import tempfile

paths = [os.path.join(tempfile.gettempdir(), f"asmpy_fix_fileinput_{n}.txt")
         for n in (1, 2)]
try:
    for path, text in zip(paths, ["a\n", "b\n"]):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
    with fileinput.input(files=paths) as stream:
        for line in stream:
            print(line.rstrip())
finally:
    for path in paths:
        if os.path.exists(path):
            os.remove(path)
''')

case("fix_csv_file_roundtrip", "csv writes and re-reads a file", r'''
import csv
import os
import tempfile

path = os.path.join(tempfile.gettempdir(), "asmpy_fix_csv.csv")
try:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["name", "count"])
        writer.writerow(["ada", 2])
    with open(path, "r", encoding="utf-8", newline="") as handle:
        print([row for row in csv.reader(handle)])
finally:
    if os.path.exists(path):
        os.remove(path)
''')

case("fix_json_file_roundtrip", "json dumps to and loads from a file", r'''
import json
import os
import tempfile

path = os.path.join(tempfile.gettempdir(), "asmpy_fix_json.json")
payload = {"name": "ada", "counts": [1, 2]}
try:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    with open(path, "r", encoding="utf-8") as handle:
        print(json.load(handle) == payload)
finally:
    if os.path.exists(path):
        os.remove(path)
''')

case("fix_pickle_file_roundtrip", "pickle dumps to and loads from a file", r'''
import os
import pickle
import tempfile

path = os.path.join(tempfile.gettempdir(), "asmpy_fix_pickle.bin")
payload = {"xs": [1, 2, 3]}
try:
    with open(path, "wb") as handle:
        pickle.dump(payload, handle)
    with open(path, "rb") as handle:
        print(pickle.load(handle) == payload)
finally:
    if os.path.exists(path):
        os.remove(path)
''')

case("fix_configparser_file_roundtrip", "configparser writes and re-reads an INI file", r'''
import configparser
import os
import tempfile

path = os.path.join(tempfile.gettempdir(), "asmpy_fix_config.ini")
try:
    writing = configparser.ConfigParser()
    writing["main"] = {"name": "ada"}
    with open(path, "w", encoding="utf-8") as handle:
        writing.write(handle)
    reading = configparser.ConfigParser()
    reading.read(path, encoding="utf-8")
    print(reading["main"]["name"])
finally:
    if os.path.exists(path):
        os.remove(path)
''')

case("fix_zipfile_file_roundtrip", "zipfile writes and re-reads an archive on disk", r'''
import os
import tempfile
import zipfile

path = os.path.join(tempfile.gettempdir(), "asmpy_fix_zip.zip")
try:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("member.txt", "inside")
    with zipfile.ZipFile(path) as archive:
        print(archive.namelist())
        print(archive.read("member.txt").decode("utf-8"))
finally:
    if os.path.exists(path):
        os.remove(path)
''')

case("fix_tarfile_roundtrip", "tarfile adds and extracts a member", r'''
import os
import shutil
import tarfile
import tempfile

root = os.path.join(tempfile.gettempdir(), "asmpy_fix_tar")
archive_path = os.path.join(root, "bundle.tar")
member_path = os.path.join(root, "member.txt")
try:
    os.makedirs(root, exist_ok=True)
    with open(member_path, "w", encoding="utf-8") as handle:
        handle.write("inside")
    with tarfile.open(archive_path, "w") as archive:
        archive.add(member_path, arcname="member.txt")
    with tarfile.open(archive_path) as archive:
        print(archive.getnames())
        print(archive.extractfile("member.txt").read().decode("utf-8"))
finally:
    shutil.rmtree(root, ignore_errors=True)
''')

case("fix_gzip_file_roundtrip", "gzip writes and re-reads a compressed file", r'''
import gzip
import os
import tempfile

path = os.path.join(tempfile.gettempdir(), "asmpy_fix_gzip.gz")
try:
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write("compressed text")
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        print(handle.read())
finally:
    if os.path.exists(path):
        os.remove(path)
''')

case("fix_shelve_roundtrip", "shelve persists a key between open calls", r'''
import os
import shelve
import shutil
import tempfile

root = os.path.join(tempfile.gettempdir(), "asmpy_fix_shelve")
path = os.path.join(root, "store")
try:
    os.makedirs(root, exist_ok=True)
    with shelve.open(path) as store:
        store["key"] = [1, 2]
    with shelve.open(path) as store:
        print(store["key"])
finally:
    shutil.rmtree(root, ignore_errors=True)
''')

case("fix_tempfile_named_file", "NamedTemporaryFile exposes a usable path", r'''
import os
import tempfile

handle = tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                     delete=False, encoding="utf-8")
path = handle.name
try:
    handle.write("named")
    handle.close()
    with open(path, "r", encoding="utf-8") as reopened:
        print(reopened.read())
    print(path.endswith(".txt"))
finally:
    if os.path.exists(path):
        os.remove(path)
''')


# ---------------------------------------------------------------------------
# sqlite3 -- the in-memory form needs no file at all
# ---------------------------------------------------------------------------

case("fix_sqlite3_in_memory", "sqlite3 round-trips a row in memory", r'''
import sqlite3

connection = sqlite3.connect(":memory:")
connection.execute("CREATE TABLE people (name TEXT, age INTEGER)")
connection.execute("INSERT INTO people VALUES (?, ?)", ("ada", 36))
for row in connection.execute("SELECT name, age FROM people"):
    print(row)
connection.close()
''')

case("fix_sqlite3_file_persists", "a sqlite3 file survives reconnecting", r'''
import os
import sqlite3
import tempfile

path = os.path.join(tempfile.gettempdir(), "asmpy_fix_sqlite.db")
try:
    first = sqlite3.connect(path)
    first.execute("CREATE TABLE t (v INTEGER)")
    first.execute("INSERT INTO t VALUES (7)")
    first.commit()
    first.close()

    second = sqlite3.connect(path)
    print(second.execute("SELECT v FROM t").fetchone()[0])
    second.close()
finally:
    if os.path.exists(path):
        os.remove(path)
''')


# ---------------------------------------------------------------------------
# clocks -- deltas and orderings only, never a value
# ---------------------------------------------------------------------------

case("fix_time_monotonic_never_goes_back", "monotonic() never decreases", r'''
import time

first = time.monotonic()
second = time.monotonic()
print(second >= first)
''')

case("fix_time_perf_counter_advances_over_work", "perf_counter advances across real work", r'''
import time

start = time.perf_counter()
total = 0
for n in range(200000):
    total += n
end = time.perf_counter()
print(total)
print(end >= start)
''')

case("fix_time_sleep_advances_monotonic", "sleep advances the monotonic clock", r'''
import time

start = time.monotonic()
time.sleep(0.01)
print(time.monotonic() - start >= 0.0)
''')

case("fix_time_time_is_after_epoch", "time() is a positive offset from the epoch", r'''
import time

print(time.time() > 1000000000.0)
''')


# ---------------------------------------------------------------------------
# subprocess, environment, and the remaining introspection modules
# ---------------------------------------------------------------------------

case("fix_subprocess_captures_stdout", "subprocess.run captures a child's stdout", r'''
import subprocess
import sys

completed = subprocess.run([sys.executable, "-c", "print('from child')"],
                           capture_output=True, text=True)
print(completed.returncode)
print(completed.stdout.strip())
''')

case("fix_subprocess_reports_exit_code", "subprocess.run reports a nonzero exit code", r'''
import subprocess
import sys

completed = subprocess.run([sys.executable, "-c", "raise SystemExit(3)"],
                           capture_output=True, text=True)
print(completed.returncode)
''')

case("fix_os_environ_roundtrip", "os.environ stores and reads a variable", r'''
import os

os.environ["ASMPY_FIX_PROBE"] = "value"
print(os.environ["ASMPY_FIX_PROBE"])
print(os.environ.get("ASMPY_FIX_ABSENT", "fallback"))
del os.environ["ASMPY_FIX_PROBE"]
print("ASMPY_FIX_PROBE" in os.environ)
''')

case("fix_importlib_import_module", "importlib.import_module loads by name", r'''
import importlib

module = importlib.import_module("json")
print(module.dumps([1, 2]))
print(module.__name__)
''')

case("fix_gc_enable_disable", "gc reports and toggles its enabled state", r'''
import gc

was_enabled = gc.isenabled()
gc.disable()
print(gc.isenabled())
gc.enable()
print(gc.isenabled())
if not was_enabled:
    gc.disable()
''')

case("fix_gc_collect_returns_count", "gc.collect returns an integer", r'''
import gc

collected = gc.collect()
print(type(collected).__name__)
print(collected >= 0)
''')

case("fix_tracemalloc_tracks_state", "tracemalloc reports whether it is tracing", r'''
import tracemalloc

print(tracemalloc.is_tracing())
tracemalloc.start()
print(tracemalloc.is_tracing())
tracemalloc.stop()
print(tracemalloc.is_tracing())
''')


if __name__ == "__main__":
    raise SystemExit(main(CASES, "gen_fixture_cases.py", sys.argv))
