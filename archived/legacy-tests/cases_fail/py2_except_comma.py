# expect-error: expected OP ':', got OP ','
try:
    raise ValueError("v")
except ValueError, e:
    print(e)
# Reduced from linguist samples/Python/tornado-httpserver.py:263
# (`except _BadRequestException, e:`) -- Python 2 except syntax must be
# rejected at parse time, not silently accepted.
