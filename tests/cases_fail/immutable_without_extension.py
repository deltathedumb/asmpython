# expect-error: requires the 'immutable' extension

@immutable
class Point:
    def __init__(self, x: int) -> None:
        self.x = x

p = Point(3)
print(p.x)
