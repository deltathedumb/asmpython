# probes: append mode adds to an existing file
# expect:
# one
# two
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
