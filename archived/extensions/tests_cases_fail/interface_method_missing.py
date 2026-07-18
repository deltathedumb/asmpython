# ext: interface
# expect-error: does not implement

interface Shape:
    def area(self) -> int:
        pass

class Circle(interface=Shape):
    def perimeter(self) -> int:
        return 1

print(Circle().perimeter())
