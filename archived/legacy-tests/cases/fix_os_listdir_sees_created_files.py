# probes: os.listdir reports created entries
# expect:
# ['a.txt', 'b.txt']
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
