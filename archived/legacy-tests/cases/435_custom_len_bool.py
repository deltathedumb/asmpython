# expect:
# 3
# 5
# True
# False

class Stack:
    def __init__(self) -> None:
        self.items: list = []

    def push(self, x: int) -> None:
        self.items.append(x)

    def __len__(self) -> int:
        return len(self.items)

    def __bool__(self) -> bool:
        return len(self.items) > 0

s = Stack()
s.push(1)
s.push(2)
s.push(3)
print(len(s))

xs = [10, 20, 30, 40, 50]
print(len(xs))

print(bool(s))
empty = Stack()
print(bool(empty))
