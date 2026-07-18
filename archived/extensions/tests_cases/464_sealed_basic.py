# ext: sealed
# expect:
# circle
# square

sealed class Shape(permits=Circle, Square):
    def kind(self) -> str:
        return "shape"

class Circle(Shape):
    def kind(self) -> str:
        return "circle"

class Square(Shape):
    def kind(self) -> str:
        return "square"

print(Circle().kind())
print(Square().kind())
