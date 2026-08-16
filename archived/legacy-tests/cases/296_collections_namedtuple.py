# expect:
# _NamedTupleBase
# alice
# 30

from collections import namedtuple, _NamedTupleBase

Person = namedtuple("Person", "name age")
print("_NamedTupleBase")
p = _NamedTupleBase("alice", "30")
print(p[0])
print(p[1])
