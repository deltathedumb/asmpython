# probes: a mutable class attribute is shared
# expect:
# ['a', 'b']
class Registry:
    entries = []

    def add(self, value):
        self.entries.append(value)


Registry().add("a")
Registry().add("b")
print(Registry.entries)
