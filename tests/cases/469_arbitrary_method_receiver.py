# expect:
# 42
# 43


class Box:
    def __init__(this, value: int) -> None:
        this.value: int = value

    def read(receiver) -> int:
        return receiver.value

    def replace(holder, value: int) -> None:
        holder.value = value


class ObjectMeta(type):
    def __new__(mcls, name, bases, namespace):
        return super().__new__(mcls, name, bases, namespace)


class Example(metaclass=ObjectMeta):
    pass


box = Box(42)
print(box.read())
box.replace(43)
print(box.read())
