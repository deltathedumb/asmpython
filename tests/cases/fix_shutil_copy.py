# probes: shutil.copy duplicates a file's contents
# expect:
# contents
import os
import shutil
import tempfile

src = os.path.join(tempfile.gettempdir(), "asmpy_fix_copy_src.txt")
dst = os.path.join(tempfile.gettempdir(), "asmpy_fix_copy_dst.txt")
try:
    with open(src, "w", encoding="utf-8") as handle:
        handle.write("contents")
    shutil.copy(src, dst)
    with open(dst, "r", encoding="utf-8") as handle:
        print(handle.read())
finally:
    for path in (src, dst):
        if os.path.exists(path):
            os.remove(path)
