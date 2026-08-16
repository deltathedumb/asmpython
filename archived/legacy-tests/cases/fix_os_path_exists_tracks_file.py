# probes: os.path.exists follows create and remove
# expect:
# True
# True
# False
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
