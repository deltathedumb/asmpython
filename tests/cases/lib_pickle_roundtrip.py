# expect:
# True
import pickle
obj = {'a': [1, 2, 3]}
s = pickle.dumps(obj)
print(pickle.loads(s) == obj)
# asmpython (beta/3.14.0) MISMATCH: prints 'False\n' (wrong).
