# ext: access
# expect:
# audited

class Base:
    @protected
    def audit(self) -> str:
        return "audited"

class Derived(Base):
    def run(self) -> str:
        return self.audit()

d = Derived()
print(d.run())
