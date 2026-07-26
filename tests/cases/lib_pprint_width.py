# expect:
# [1, 2, 3]
import pprint
print(pprint.pformat([1, 2, 3], width=20))
# asmpython (beta/3.14.0) MISMATCH: prints '0\n' (wrong).
