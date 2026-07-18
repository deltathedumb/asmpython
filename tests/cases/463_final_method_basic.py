# ext: final
# expect:
# core

class Base:
    @final
    def core(self) -> str:
        return "core"

class Derived(Base):
    def other(self) -> int:
        return 1

d = Derived()
print(d.core())
