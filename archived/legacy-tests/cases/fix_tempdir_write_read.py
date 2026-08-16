# probes: a file can be written under the temp dir and read back
# expect:
# payload
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
