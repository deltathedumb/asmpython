# ext: interface
# expect-error: collides with existing name

interface Shape:
    def area(self) -> int:
        pass

interface Shape:
    def perimeter(self) -> int:
        pass

print(1)
