# probes: an exception carries its message
# expect:
# boom
# 4
try:
    raise ValueError("boom")
except ValueError as e:
    print(str(e))
    print(len(str(e)))
