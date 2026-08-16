# probes: SimpleNamespace takes keyword fields
# expect:
# 1
# two
import types

ns = types.SimpleNamespace(a=1, b="two")
print(ns.a)
print(ns.b)
