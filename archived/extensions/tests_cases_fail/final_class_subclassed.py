# ext: final
# expect-error: it is a 'final class'

final class Point:
    def describe(self) -> str:
        return "origin"

class Point3D(Point):
    def extra(self) -> int:
        return 1

p = Point3D()
print(p.extra())
