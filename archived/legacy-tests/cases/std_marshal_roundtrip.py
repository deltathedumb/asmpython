# probes: marshal round-trips a simple structure
# expect:
# [1, 2, 3]
import marshal

print(marshal.loads(marshal.dumps([1, 2, 3])))
