# tier: spec
# ref: reference/datamodel.html#object.__bool__
# expect:
# False
# True
class Empty:
    def __len__(self):
        return 0

class NeverFalse:
    def __bool__(self):
        return True
    def __len__(self):
        return 0

print(bool(Empty()))
print(bool(NeverFalse()))
