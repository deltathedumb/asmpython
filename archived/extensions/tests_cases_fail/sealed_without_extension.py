# expect-error: requires the 'sealed' extension

sealed class Shape(permits=Circle):
    def kind(self) -> str:
        return "shape"

class Circle(Shape):
    def kind(self) -> str:
        return "circle"

print(Circle().kind())
