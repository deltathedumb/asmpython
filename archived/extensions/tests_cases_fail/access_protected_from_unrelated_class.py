# ext: access
# expect-error: is protected

class Base:
    @protected
    def audit(self) -> int:
        return 1

class Unrelated:
    def run(self, b: Base) -> int:
        return b.audit()

u = Unrelated()
print(u.run(Base()))
