# expect:
# 42
# 100
# hello

import pickle

s1: str = pickle.dumps_int(42)
s2: str = pickle.dumps_int(100)
s3: str = pickle.dumps_str("hello")

v1: int = pickle.loads_int(s1)
v2: int = pickle.loads_int(s2)
v3: str = pickle.loads_str(s3)

print(v1)
print(v2)
print(v3)
