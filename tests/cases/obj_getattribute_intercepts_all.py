# probes: __getattribute__ sees every read
# expect:
# 99
class Watched:
    def __init__(self):
        object.__setattr__(self, "value", 7)

    def __getattribute__(self, name):
        if name == "value":
            return 99
        return object.__getattribute__(self, name)


print(Watched().value)
