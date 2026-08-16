# probes: a property is read without a call
# expect:
# 6
class Circle:
    def __init__(self, r):
        self.r = r

    @property
    def diameter(self):
        return self.r * 2


print(Circle(3).diameter)
