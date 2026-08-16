# probes: linecache reads a numbered line from a file
# expect:
# beta
# True
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
