# expect-error: requires the 'final' extension

final class Point:
    def describe(self) -> str:
        return "origin"

p = Point()
print(p.describe())
