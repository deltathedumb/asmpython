# tier: spec
# ref: reference/datamodel.html#object.__set_name__
# expect:
# field:a
# field:b
class Field:
    def __set_name__(self, owner, name):
        self.name = name
    def __get__(self, obj, objtype=None):
        return f"field:{self.name}"

class C:
    a = Field()
    b = Field()

print(C().a)
print(C().b)
