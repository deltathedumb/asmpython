# probes: a subclass inherits an undefined method
# expect:
# base
class Base:
    def speak(self):
        return "base"


class Child(Base):
    pass


print(Child().speak())
