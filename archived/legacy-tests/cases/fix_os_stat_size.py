# probes: os.path.getsize reports the byte count
# expect:
# 5
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
