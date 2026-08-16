# expect:
# 42


class Value:
    def __init__(self, number: int) -> None:
        self.number: int = number

    def read(self) -> int:
        return self.number


def read_value(value):
    return value.read()


print(read_value(Value(42)))
