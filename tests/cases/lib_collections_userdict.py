# expect:
# 1
from collections import UserDict
class MyDict(UserDict):
    pass
d = MyDict()
d['a'] = 1
print(d['a'])
# asmpython (beta/3.14.0) rejects at compile: [E043] 'MyDict' object does not support index assignment
