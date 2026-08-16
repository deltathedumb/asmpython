# probes: readlines splits a file into lines
# expect:
# ['a\n', 'b\n', 'c\n']
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
