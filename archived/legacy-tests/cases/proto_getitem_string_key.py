# probes: __getitem__ serves non-integer keys
# expect:
# value-for-name
class Lookup:
    def __getitem__(self, key):
        return "value-for-" + key


print(Lookup()["name"])
