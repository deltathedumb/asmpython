# probes: a property deleter runs on del
# expect:
# deleted
class Slot:
    def __init__(self):
        self._v = "set"

    @property
    def v(self):
        return self._v

    @v.deleter
    def v(self):
        self._v = "deleted"


s = Slot()
del s.v
print(s._v)
