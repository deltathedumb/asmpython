# probes: os.environ stores and reads a variable
# expect:
# value
# fallback
# False
import os

os.environ["ASMPY_FIX_PROBE"] = "value"
print(os.environ["ASMPY_FIX_PROBE"])
print(os.environ.get("ASMPY_FIX_ABSENT", "fallback"))
del os.environ["ASMPY_FIX_PROBE"]
print("ASMPY_FIX_PROBE" in os.environ)
