class Foo:
    _STUBS = ("a", "b", "c")
    def get_set(self) -> set:
        return set(self._STUBS)

f = Foo()
s = f.get_set()
print("a" in s)
