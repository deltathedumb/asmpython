# expect:
# 3.5
# 6
# 8
print(1.5 + 2)

class Base:
    def __init__(self, value):
        self.value = value

    def doubled(self):
        return self.value * 2

class Child(Base):
    pass

item = Child(3)
print(item.doubled())
item.value = 4
print(item.value * 2)
