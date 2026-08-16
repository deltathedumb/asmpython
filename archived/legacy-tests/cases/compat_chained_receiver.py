# guards: chained_receiver_compat_fixes
# expect:
# inner:a
# a
class Inner:
    def __init__(self, tag):
        self.tag = tag

    def label(self):
        return "inner:" + self.tag


class Outer:
    def __init__(self):
        self._inner = Inner("a")

    @property
    def inner(self):
        return self._inner


o = Outer()
print(o.inner.label())
print(o.inner.tag)
