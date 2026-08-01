# probes: a dict subclass inherits dict behaviour
# expect:
# 1
# 1
# default
class Config(dict):
    def get_or(self, key, fallback):
        return self[key] if key in self else fallback


c = Config()
c["a"] = 1
print(c["a"])
print(len(c))
print(c.get_or("b", "default"))
