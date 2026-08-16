# expect:
# 1
# 2
# 3
# done
# 1
# 4
# 9

class Counter:
    def __init__(self, start: int, stop: int) -> None:
        self.current = start
        self.stop = stop

    def __iter__(self) -> "Counter":
        return self

    def __next__(self) -> int:
        if self.current >= self.stop:
            raise StopIteration
        val: int = self.current
        self.current += 1
        return val

c = Counter(1, 4)
for v in c:
    print(v)
print("done")

squares = [x * x for x in Counter(1, 4)]
for v in squares:
    print(v)
