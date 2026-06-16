# probe56: property decorators, class methods
class Temperature:
    def __init__(self, celsius: float) -> None:
        self._celsius = celsius

    @property
    def celsius(self) -> float:
        return self._celsius

    @property
    def fahrenheit(self) -> float:
        return self._celsius * 9.0 / 5.0 + 32.0

    @celsius.setter
    def celsius(self, value: float) -> None:
        self._celsius = value

t = Temperature(100.0)
print(t.celsius)      # 100.0
print(t.fahrenheit)   # 212.0

t.celsius = 0.0
print(t.celsius)      # 0.0
print(t.fahrenheit)   # 32.0

# class method / static method
class Counter:
    count: int = 0

    @classmethod
    def increment(cls) -> None:
        cls.count += 1

    @classmethod
    def get(cls) -> int:
        return cls.count

Counter.increment()
Counter.increment()
Counter.increment()
print(Counter.get())  # 3
