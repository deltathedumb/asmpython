# probe120: complex interaction of features - real-world patterns

# Priority queue using heapq-like pattern (manual)
class MinHeap:
    def __init__(self) -> None:
        self._data: list = []

    def push(self, val: int) -> None:
        self._data.append(val)
        i: int = len(self._data) - 1
        while i > 0:
            parent: int = (i - 1) // 2
            if self._data[parent] > self._data[i]:
                self._data[parent], self._data[i] = self._data[i], self._data[parent]
                i = parent
            else:
                break

    def pop(self) -> int:
        if len(self._data) == 0:
            raise IndexError("empty heap")
        val: int = self._data[0]
        last: int = self._data.pop()
        if self._data:
            self._data[0] = last
            i: int = 0
            n: int = len(self._data)
            while True:
                left: int = 2 * i + 1
                right: int = 2 * i + 2
                smallest: int = i
                if left < n and self._data[left] < self._data[smallest]:
                    smallest = left
                if right < n and self._data[right] < self._data[smallest]:
                    smallest = right
                if smallest == i:
                    break
                self._data[i], self._data[smallest] = self._data[smallest], self._data[i]
                i = smallest
        return val

    def __len__(self) -> int:
        return len(self._data)

h = MinHeap()
for v in [5, 3, 8, 1, 9, 2, 7, 4, 6]:
    h.push(v)

print(len(h))   # 9
result = []
while len(h) > 0:
    result.append(h.pop())

print(result)   # [1, 2, 3, 4, 5, 6, 7, 8, 9]
