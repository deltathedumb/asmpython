# probe70: __len__, __contains__, __iter__, __getitem__ on classes
class Stack:
    def __init__(self) -> None:
        self._data: list = []

    def push(self, v: int) -> None:
        self._data.append(v)

    def pop(self) -> int:
        return self._data.pop()

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, v: int) -> bool:
        for x in self._data:
            if x == v:
                return True
        return False

    def peek(self) -> int:
        return self._data[len(self._data) - 1]

s = Stack()
s.push(10)
s.push(20)
s.push(30)
print(len(s))       # 3
print(10 in s)      # True
print(99 in s)      # False
print(s.peek())     # 30
print(s.pop())      # 30
print(len(s))       # 2

# __getitem__
class Vec:
    def __init__(self, x: int, y: int, z: int) -> None:
        self.x = x
        self.y = y
        self.z = z

    def __getitem__(self, i: int) -> int:
        if i == 0:
            return self.x
        if i == 1:
            return self.y
        return self.z

    def __len__(self) -> int:
        return 3

v = Vec(1, 2, 3)
print(v[0])   # 1
print(v[1])   # 2
print(v[2])   # 3
print(len(v)) # 3
