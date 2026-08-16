# probes: a file object iterates line by line
# expect:
# first
# second
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
