# probes: glob matches files by pattern
# expect:
# ['a.py', 'c.py']
import glob
import os
import shutil
import tempfile

root = os.path.join(tempfile.gettempdir(), "asmpy_fix_glob")
try:
    os.makedirs(root, exist_ok=True)
    for name in ["a.py", "b.txt", "c.py"]:
        with open(os.path.join(root, name), "w", encoding="utf-8") as handle:
            handle.write("x")
    hits = glob.glob(os.path.join(root, "*.py"))
    print(sorted(os.path.basename(p) for p in hits))
finally:
    shutil.rmtree(root, ignore_errors=True)
