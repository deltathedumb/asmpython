# tier: spec
# ref: library/sys.html#sys.stdout
# expect:
# 'captured\n'
# True True
# StringIO
import io
import sys

buf = io.StringIO()
old = sys.stdout
sys.stdout = buf
print("captured")
sys.stdout = old
print(repr(buf.getvalue()))
print(hasattr(sys.stdout, "write"), hasattr(sys.stdout, "flush"))
print(type(io.StringIO()).__name__)
