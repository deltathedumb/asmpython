# probes: contextlib.suppress swallows the named error
# expect:
# continued
import contextlib

with contextlib.suppress(ValueError):
    raise ValueError("ignored")
print("continued")
