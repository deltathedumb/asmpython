# probes: callable() reflects __call__
# expect:
# True
# False
# True
class WithCall:
    def __call__(self):
        return 1


class WithoutCall:
    pass


print(callable(WithCall()))
print(callable(WithoutCall()))
print(callable(WithoutCall))
