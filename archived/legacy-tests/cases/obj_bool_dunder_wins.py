# probes: __bool__ overrides __len__ for truthiness
# expect:
# True
class Odd:
    def __len__(self):
        return 0

    def __bool__(self):
        return True


print(bool(Odd()))
