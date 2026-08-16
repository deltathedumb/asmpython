# probes: json dumps to and loads from a file
# expect:
# True
import json
import os
import tempfile

path = os.path.join(tempfile.gettempdir(), "asmpy_fix_json.json")
payload = {"name": "ada", "counts": [1, 2]}
try:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    with open(path, "r", encoding="utf-8") as handle:
        print(json.load(handle) == payload)
finally:
    if os.path.exists(path):
        os.remove(path)
