# expect:
# 42
# Valid Python class-header keyword regression.


class Item(metaclass=type):
    value = 42


print(Item.value)
