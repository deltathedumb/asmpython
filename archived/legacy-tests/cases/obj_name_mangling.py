# probes: a __private name is mangled per class
# expect:
# hidden
# True
# False
class Holder:
    def __init__(self):
        self.__secret = "hidden"

    def reveal(self):
        return self.__secret


h = Holder()
print(h.reveal())
print(hasattr(h, "_Holder__secret"))
print(hasattr(h, "__secret"))
