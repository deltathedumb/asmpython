# expect:
# 42


class Item:
    def __init__(self, value: int) -> None:
        self.value: int = value


def choose_type():
    return Item


item = choose_type()(42)
print(item.value)
