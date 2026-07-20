# expect:
# 3
# 5


class Item:
    def __init__(self, value: int = 0) -> None:
        self.value: int = value
        self.children = []

    def add_child(self, child) -> None:
        self.children.append(child)

    def walk(self, include_self: bool = True):
        if include_self:
            yield self
        for child in self.children:
            yield from child.walk()


class Special(Item):
    pass


class Services(Item):
    def get_service(self, service_type):
        for child in self.children:
            if isinstance(child, service_type):
                return child
        return None

    def ensure_service(self, service_type):
        existing = self.get_service(service_type)
        if existing is not None:
            return existing
        service = service_type(5)
        self.add_child(service)
        return service


root = Services(1)
root.add_child(Item(2))
root.add_child(Item(3))
print(len(root.walk()))
service = root.ensure_service(Special)
print(service.value)
