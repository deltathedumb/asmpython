# probes: __init_subclass__ runs for each subclass
# expect:
# ['A', 'B']
class Registry:
    seen = []

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        Registry.seen.append(cls.__name__)


class A(Registry):
    pass


class B(Registry):
    pass


print(Registry.seen)
