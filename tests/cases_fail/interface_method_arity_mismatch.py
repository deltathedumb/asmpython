# ext: interface
# expect-error: parameter(s), but interface

interface Shape:
    def scale(self, factor: int) -> int:
        pass

class Circle(interface=Shape):
    def scale(self) -> int:
        return 1

print(Circle().scale())
