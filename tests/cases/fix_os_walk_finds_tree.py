# probes: os.walk enumerates a directory tree
# expect:
# ['deep.txt', 'top.txt']
import os
import shutil
import tempfile

root = os.path.join(tempfile.gettempdir(), "asmpy_fix_walk")
try:
    os.makedirs(os.path.join(root, "sub"), exist_ok=True)
    with open(os.path.join(root, "top.txt"), "w", encoding="utf-8") as handle:
        handle.write("x")
    with open(os.path.join(root, "sub", "deep.txt"), "w", encoding="utf-8") as handle:
        handle.write("y")
    found = []
    for _, _, files in os.walk(root):
        found.extend(files)
    print(sorted(found))
finally:
    shutil.rmtree(root, ignore_errors=True)
