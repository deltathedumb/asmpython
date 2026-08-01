# tier: spec
# ref: reference/datamodel.html#object.__mro_entries__
# expect:
# Base
# Fake
class Fake:
    def __mro_entries__(self, bases):
        return (Base,)

class Base:
    pass

class C(Fake()):
    pass

print(C.__mro__[1].__name__)
print(C.__orig_bases__[0].__class__.__name__)
