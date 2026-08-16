# expect:
# 20
# 68
# 100
# 212
# 0
# Rex
# Fido


class Temperature:
    def __init__(self, celsius: int) -> None:
        self._celsius = celsius

    @property
    def celsius(self) -> int:
        return self._celsius

    @celsius.setter
    def celsius(self, value: int) -> None:
        self._celsius = value

    @property
    def fahrenheit(self) -> int:
        return self._celsius * 9 // 5 + 32

    @fahrenheit.setter
    def fahrenheit(self, value: int) -> None:
        self._celsius = (value - 32) * 5 // 9


t = Temperature(20)
print(t.celsius)
print(t.fahrenheit)
t.celsius = 100
print(t.celsius)
print(t.fahrenheit)
t.fahrenheit = 32
print(t.celsius)


class Animal:
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str) -> None:
        self._name = value


class Dog(Animal):
    pass


d = Dog("Rex")
print(d.name)
d.name = "Fido"
print(d.name)
