# tier: spec
# ref: reference/datamodel.html#object.__repr__
# expect:
# <only-repr>
# <only-repr>
# <str>
# <repr>
# [<repr>]
class OnlyRepr:
    def __repr__(self):
        return "<only-repr>"

class Both:
    def __repr__(self):
        return "<repr>"
    def __str__(self):
        return "<str>"

print(str(OnlyRepr()))
print(repr(OnlyRepr()))
print(str(Both()))
print(repr(Both()))
print([Both()])
