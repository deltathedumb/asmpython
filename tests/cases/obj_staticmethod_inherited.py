# probes: a subclass inherits a staticmethod
# expect:
# helper
# helper
class Base:
    @staticmethod
    def helper():
        return "helper"


class Child(Base):
    pass


print(Child.helper())
print(Child().helper())
