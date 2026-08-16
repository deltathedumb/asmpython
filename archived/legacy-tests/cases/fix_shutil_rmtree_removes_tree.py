# probes: shutil.rmtree removes a populated directory
# expect:
# True
# False
import os
import shutil
import tempfile

root = os.path.join(tempfile.gettempdir(), "asmpy_fix_rmtree")
os.makedirs(root, exist_ok=True)
with open(os.path.join(root, "f.txt"), "w", encoding="utf-8") as handle:
    handle.write("x")
print(os.path.isdir(root))
shutil.rmtree(root)
print(os.path.isdir(root))
