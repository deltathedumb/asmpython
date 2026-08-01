# probes: pickle dumps to and loads from a file
# expect:
# True
import os
import pickle
import tempfile

path = os.path.join(tempfile.gettempdir(), "asmpy_fix_pickle.bin")
payload = {"xs": [1, 2, 3]}
try:
    with open(path, "wb") as handle:
        pickle.dump(payload, handle)
    with open(path, "rb") as handle:
        print(pickle.load(handle) == payload)
finally:
    if os.path.exists(path):
        os.remove(path)
