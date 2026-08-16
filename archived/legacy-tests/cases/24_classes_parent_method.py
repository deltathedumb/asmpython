# expect:
# 10
# 20
# greet from Base
# greet from Base
class Base:
    def __init__(self, n):
        self.n = n

    def double(self):
        return self.n + self.n

    def greet(self):
        print("greet from Base")
        return 0

class Child(Base):
    # Child does NOT override double() or greet(); they're inherited.
    def __init__(self, n):
        self.n = n

b = Base(5)
print(b.double())

c = Child(10)
print(c.double())

b.greet()
c.greet()
