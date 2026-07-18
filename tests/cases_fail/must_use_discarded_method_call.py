# ext: must_use
# expect-error: is discarded, but it is marked @must_use

class Calc:
    def __init__(self) -> None:
        self.total = 0

    @must_use
    def compute(self) -> int:
        return self.total + 1

c = Calc()
c.compute()
