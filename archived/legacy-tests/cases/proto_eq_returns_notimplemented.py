# probes: NotImplemented falls through to identity
# expect:
# True
# False
# True
class Picky:
    def __eq__(self, other):
        if not isinstance(other, Picky):
            return NotImplemented
        return True


p = Picky()
print(p == Picky())
print(p == 1)
print(p == p)
