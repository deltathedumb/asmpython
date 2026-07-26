# expect:
# [0, 1, 2, 3, 4, 5, .
import reprlib
print(reprlib.repr(list(range(100)))[:20])
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
