# probes: print prefers __str__ over __repr__
# expect:
# <str>
# <repr>
class Tagged:
    def __repr__(self):
        return "<repr>"

    def __str__(self):
        return "<str>"


print(Tagged())
print(repr(Tagged()))
