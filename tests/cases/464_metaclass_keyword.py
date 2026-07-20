# expect:
# 42


class Item(metaclass=type):
    value = 42


print(Item.value)
