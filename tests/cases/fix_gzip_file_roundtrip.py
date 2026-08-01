# probes: gzip writes and re-reads a compressed file
# expect:
# compressed text
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
