# probes: NamedTemporaryFile exposes a usable path
# expect:
# named
# True
import os
import tempfile

handle = tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                     delete=False, encoding="utf-8")
path = handle.name
try:
    handle.write("named")
    handle.close()
    with open(path, "r", encoding="utf-8") as reopened:
        print(reopened.read())
    print(path.endswith(".txt"))
finally:
    if os.path.exists(path):
        os.remove(path)
