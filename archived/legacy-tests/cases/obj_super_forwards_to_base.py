# probes: super() reaches the shadowed base method
# expect:
# child+base
class Base:
    def speak(self):
        return "base"


class Child(Base):
    def speak(self):
        return "child+" + super().speak()


print(Child().speak())
