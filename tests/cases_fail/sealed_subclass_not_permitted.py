# ext: sealed
# expect-error: not in its permits list

sealed class Shape(permits=Circle):
    def kind(self) -> str:
        return "shape"

class Circle(Shape):
    def kind(self) -> str:
        return "circle"

class Triangle(Shape):
    def kind(self) -> str:
        return "triangle"

print(Triangle().kind())
