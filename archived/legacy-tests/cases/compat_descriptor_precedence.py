# guards: descriptor_precedence_compat_fixes
# expect:
# 10
# 20
class Doubling:
    def __init__(self):
        self._store = {}

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        return self._store.get(id(obj), 0)

    def __set__(self, obj, value):
        self._store[id(obj)] = value * 2


class Thing:
    amount = Doubling()


t = Thing()
t.amount = 5
print(t.amount)
t.amount = 10
print(t.amount)
