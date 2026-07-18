# ext: final
# expect:
# origin

final class Point:
    def describe(self) -> str:
        return "origin"

p = Point()
print(p.describe())
