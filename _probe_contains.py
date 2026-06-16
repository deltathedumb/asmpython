# expect:
# True
# False
# True
# True
# False

class Bag:
    def __init__(self) -> None:
        self.items: list = []

    def add(self, x: int) -> None:
        self.items.append(x)

    def __contains__(self, x: int) -> bool:
        for item in self.items:
            if item == x:
                return True
        return False

b = Bag()
b.add(1)
b.add(2)
b.add(3)
print(1 in b)
print(5 in b)

# Also test built-in containment
xs = [10, 20, 30]
print(20 in xs)
d = {"a": 1, "b": 2}
print("a" in d)
print("c" in d)
