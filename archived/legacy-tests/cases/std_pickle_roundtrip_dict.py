# probes: pickle round-trips a dict
# expect:
# True
import pickle

original = {"a": 1, "b": [2, 3]}
print(pickle.loads(pickle.dumps(original)) == original)
