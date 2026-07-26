# expect-error: undefined function 'unicode'
x = unicode("hi", "utf-8")
print(x)
# Reduced from linguist samples/Python/protocol_buffer_pb2.py:31
# (`default_value=unicode("", "utf-8")`) -- the Python 2 `unicode` builtin
# does not exist in Python 3 and must be reported, not stubbed.
