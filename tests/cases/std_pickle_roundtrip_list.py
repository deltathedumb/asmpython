# probes: pickle round-trips a list
# expect:
# [1, 'two', 3.0]
import pickle

print(pickle.loads(pickle.dumps([1, "two", 3.0])))
