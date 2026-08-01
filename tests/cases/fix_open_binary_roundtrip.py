# probes: binary mode round-trips exact bytes
# expect:
# 4
# True
import os
import tempfile

path = os.path.join(tempfile.gettempdir(), "asmpy_fix_bin.dat")
payload = b"\x00\x01\xfe\xff"
try:
    with open(path, "wb") as handle:
        handle.write(payload)
    with open(path, "rb") as handle:
        read_back = handle.read()
    print(len(read_back))
    print(read_back == payload)
finally:
    if os.path.exists(path):
        os.remove(path)
