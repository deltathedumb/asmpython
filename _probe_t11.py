# property decorator
class Circle:
    def __init__(self, radius: float) -> None:
        self._radius = radius
    
    @property
    def radius(self) -> float:
        return self._radius
    
    @radius.setter
    def radius(self, value: float) -> None:
        if value < 0.0:
            raise ValueError("radius cannot be negative")
        self._radius = value
    
    @property
    def area(self) -> float:
        return 3.14159 * self._radius * self._radius

c = Circle(5.0)
print(c.radius)
print(c.area)
c.radius = 10.0
print(c.radius)
