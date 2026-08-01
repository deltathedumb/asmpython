# tier: spec
# ref: reference/datamodel.html#custom-classes
# expect:
# hi base
# hi sub
class Base:
    def name(self):
        return "base"
    def greet(self):
        return "hi " + self.name()

class Sub(Base):
    def name(self):
        return "sub"

print(Base().greet())
print(Sub().greet())
