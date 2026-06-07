# expect:
# 10
# 20
# 30
# 7
@dataclass
class Point:
    """class-level docstring is dropped silently."""

    x: int
    y: int = 0
    name: str

    def show(self):
        """method-level docstring lives in the body."""
        print(self.x)
        print(self.y)

p = Point()
p.x = 10
p.y = 20
p.show()
print(30)


@staticmethod
def add(a, b=2):
    return a + b


print(add(5))
