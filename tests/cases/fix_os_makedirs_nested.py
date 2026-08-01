# probes: makedirs creates intermediate directories
# expect:
# True
import os
import shutil
import tempfile

root = os.path.join(tempfile.gettempdir(), "asmpy_fix_mkdirs")
deep = os.path.join(root, "a", "b")
try:
    os.makedirs(deep, exist_ok=True)
    print(os.path.isdir(deep))
finally:
    shutil.rmtree(root, ignore_errors=True)
