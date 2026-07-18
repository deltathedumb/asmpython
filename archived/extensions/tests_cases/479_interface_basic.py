# ext: interface
# expect:
# 1
# 4

interface Shape:
    def area(self) -> int:
        pass

class Circle(interface=Shape):
    def area(self) -> int:
        return 1

class Square(interface=Shape):
    def area(self) -> int:
        return 4

print(Circle().area())
print(Square().area())
