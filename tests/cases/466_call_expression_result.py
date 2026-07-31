# expect:
# 42
# 43
# Covers a callable returned by a function and type(instance)(...) cloning.


class Item:
    def __init__(self, value: int) -> None:
        self.value: int = value


class Factory:
    def __call__(self, value: int) -> Item:
        return Item(value)


def make_factory() -> Factory:
    return Factory()


item = make_factory()(42)
print(item.value)
source = Item(1)
clone = type(source)(43)
print(clone.value)
