# expect:
# [1, 2, 3]
import marshal
s = marshal.dumps([1, 2, 3])
print(marshal.loads(s))
# asmpython (beta/3.14.0) MISMATCH: prints '0\n' (wrong).
