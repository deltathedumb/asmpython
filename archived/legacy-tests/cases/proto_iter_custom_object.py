# probes: __iter__/__next__ drive a for loop
# expect:
# 1
# 2
# 3
class UpTo:
    def __init__(self, limit):
        self.limit = limit
        self.current = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self.current >= self.limit:
            raise StopIteration
        self.current = self.current + 1
        return self.current


for v in UpTo(3):
    print(v)
