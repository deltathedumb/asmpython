# probes: opening a missing file raises FileNotFoundError
# expect:
# refused
import os
import tempfile

path = os.path.join(tempfile.gettempdir(), "asmpy_fix_absent.txt")
if os.path.exists(path):
    os.remove(path)
try:
    open(path, "r", encoding="utf-8")
    print("opened")
except FileNotFoundError:
    print("refused")
