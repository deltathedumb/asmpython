# expect-error: requires the 'final' extension

class Base:
    @final
    def core(self) -> int:
        return 1

b = Base()
print(b.core())
