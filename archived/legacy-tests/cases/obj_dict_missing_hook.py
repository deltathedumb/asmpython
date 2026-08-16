# probes: __missing__ handles an absent dict key
# expect:
# real
# default-absent
class Defaulting(dict):
    def __missing__(self, key):
        return "default-" + key


d = Defaulting()
d["present"] = "real"
print(d["present"])
print(d["absent"])
