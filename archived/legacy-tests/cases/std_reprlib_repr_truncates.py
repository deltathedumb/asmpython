# probes: reprlib.repr abbreviates a long list
# expect:
# [0, 1, 2, 3, 4, 5, ...]
import reprlib

print(reprlib.repr(list(range(20))))
