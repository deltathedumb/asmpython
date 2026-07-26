# expect:
# 1 2
from types import SimpleNamespace
ns = SimpleNamespace(x=1, y=2)
print(ns.x, ns.y)
# asmpython (beta/3.14.0) rejects at compile: [E021] SimpleNamespace() got an unexpected keyword argument 'x'
