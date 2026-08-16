# probes: os.rename moves a file
# expect:
# False
# moved
import os
import tempfile

src = os.path.join(tempfile.gettempdir(), "asmpy_fix_rename_a.txt")
dst = os.path.join(tempfile.gettempdir(), "asmpy_fix_rename_b.txt")
try:
    with open(src, "w", encoding="utf-8") as handle:
        handle.write("moved")
    os.rename(src, dst)
    print(os.path.exists(src))
    with open(dst, "r", encoding="utf-8") as handle:
        print(handle.read())
finally:
    for path in (src, dst):
        if os.path.exists(path):
            os.remove(path)
