# expect:
# <class 'int'>
# <class 'float'>
# <class 'str'>
# <class 'list'>
# <class 'dict'>
# <class 'tuple'>
# <class 'set'>
# <class '__main__.Point'>


class Point:
    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y


print(type(1))
print(type(1.5))
print(type("hi"))
print(type([1, 2]))
print(type({"a": 1}))
print(type((1, 2)))
print(type({"a", "b"}))

p = Point(1, 2)
print(type(p))
