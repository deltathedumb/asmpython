# expect:
# somnia.Root


class Value:
    def __init__(self, name):
        self.name = name


class Registry:
    def __init__(self):
        self.names = {"Root": "somnia.Root"}

    def type_name(self, value):
        return self.names.get(value.name, value.name)


registry = Registry()
print(registry.type_name(Value("Root")))
