# probes: a subclass method shadows the base method
# expect:
# child
# base
class Base:
    def speak(self):
        return "base"


class Child(Base):
    def speak(self):
        return "child"


print(Child().speak())
print(Base().speak())
