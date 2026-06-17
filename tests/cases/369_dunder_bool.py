# expect:
# b1 truthy
# b0 falsy
# empty stack falsy
# stack with 1 truthy
# plain truthy
# 3
# 2
# 1
# done
# c2 is falsy

from __future__ import annotations

class Box:
    def __init__(self, value: int) -> None:
        self.value = value

    def __bool__(self) -> int:
        return self.value != 0

class Stack:
    def __init__(self) -> None:
        self.size = 0

    def push(self) -> None:
        self.size = self.size + 1

    def __len__(self) -> int:
        return self.size

class Plain:
    def __init__(self, x: int) -> None:
        self.x = x

b1: Box = Box(5)
b0: Box = Box(0)
if b1:
    print("b1 truthy")
else:
    print("b1 falsy")
if b0:
    print("b0 truthy")
else:
    print("b0 falsy")

s: Stack = Stack()
if s:
    print("empty stack truthy")
else:
    print("empty stack falsy")
s.push()
if s:
    print("stack with 1 truthy")
else:
    print("stack with 1 falsy")

p: Plain = Plain(0)
if p:
    print("plain truthy")
else:
    print("plain falsy")

class Counter:
    def __init__(self, n: int) -> None:
        self.n = n

    def __bool__(self) -> int:
        return self.n > 0

    def tick(self) -> None:
        self.n = self.n - 1

c: Counter = Counter(3)
while c:
    print(c.n)
    c.tick()
print("done")

c2: Counter = Counter(0)
if not c2:
    print("c2 is falsy")
