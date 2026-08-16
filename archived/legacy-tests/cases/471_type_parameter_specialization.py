# expect:
# 42
# True


class Item:
    def __init__(self, value=0):
        self.value = value


class Store:
    def __init__(self):
        self.items: list = []

    def find(self, object_type):
        for item in self.items:
            if isinstance(item, object_type):
                return item
        return None

    def ensure(self, object_type):
        existing = self.find(object_type)
        if existing is not None:
            return existing
        created = object_type(value=42)
        self.items.append(created)
        return created


store = Store()
item = store.ensure(Item)
print(item.value)
print(store.find(Item) is item)
