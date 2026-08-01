# probes: fileinput concatenates several files
# expect:
# a
# b
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
