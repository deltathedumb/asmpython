# ext: final
# expect-error: it is declared @final

class Base:
    @final
    def core(self) -> str:
        return "base"

class Derived(Base):
    def core(self) -> str:
        return "derived"

d = Derived()
print(d.core())
