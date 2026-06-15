# expect:
# 0
# 2

import weakref

x: int = 42
print(weakref.getweakrefcount(x))

ws = weakref.WeakSet()
ws.add(1)
ws.add(2)
ws.add(1)
print(len(ws))
