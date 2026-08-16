# probes: shelve persists a key between open calls
# expect:
# [1, 2]
import os
import shelve
import shutil
import tempfile

root = os.path.join(tempfile.gettempdir(), "asmpy_fix_shelve")
path = os.path.join(root, "store")
try:
    os.makedirs(root, exist_ok=True)
    with shelve.open(path) as store:
        store["key"] = [1, 2]
    with shelve.open(path) as store:
        print(store["key"])
finally:
    shutil.rmtree(root, ignore_errors=True)
