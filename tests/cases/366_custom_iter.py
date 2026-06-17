# expect:
# 3
# 10
# 10
# 20
# 30

class MyList:
    def __init__(self) -> None:
        self._data: list[int] = []

    def append(self, x: int) -> None:
        self._data.append(x)

    def __len__(self) -> int:
        return len(self._data)

    def __getitem__(self, i: int) -> int:
        return self._data[i]

    def __iter__(self) -> MyList:
        self._idx: int = 0
        return self

    def __next__(self) -> int:
        if self._idx >= len(self._data):
            raise StopIteration("done")
        v: int = self._data[self._idx]
        self._idx = self._idx + 1
        return v

m: MyList = MyList()
m.append(10)
m.append(20)
m.append(30)
print(len(m))
print(m[0])
for x in m:
    print(x)
