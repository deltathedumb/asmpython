# probe96: complex class patterns - property with setter, __setattr__
class Temperature:
    def __init__(self, celsius: int) -> None:
        self._celsius = celsius

    @property
    def celsius(self) -> int:
        return self._celsius

    @celsius.setter
    def celsius(self, value: int) -> None:
        if value < -273:
            raise ValueError("below absolute zero")
        self._celsius = value

    @property
    def fahrenheit(self) -> int:
        return self._celsius * 9 // 5 + 32

t = Temperature(100)
print(t.celsius)      # 100
print(t.fahrenheit)   # 212

t.celsius = 0
print(t.celsius)      # 0
print(t.fahrenheit)   # 32

t.celsius = 37
print(t.celsius)      # 37
print(t.fahrenheit)   # 98

try:
    t.celsius = -300
except ValueError as e:
    print(str(e))   # below absolute zero
