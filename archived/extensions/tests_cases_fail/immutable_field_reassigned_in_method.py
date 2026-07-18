# ext: immutable
# expect-error: is immutable outside

@immutable
class Point:
    def __init__(self, x: int) -> None:
        self.x = x

    def bump(self) -> None:
        self.x = self.x + 1

p = Point(3)
p.bump()
print(p.x)
