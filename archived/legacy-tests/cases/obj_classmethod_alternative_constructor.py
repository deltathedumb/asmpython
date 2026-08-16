# probes: a classmethod can build an instance
# expect:
# 0
# 0
class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y

    @classmethod
    def origin(cls):
        return cls(0, 0)


p = Point.origin()
print(p.x)
print(p.y)
