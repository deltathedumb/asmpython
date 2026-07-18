# ext: access
# expect-error: is private

class Base:
    @private
    def secret(self) -> int:
        return 1

class Derived(Base):
    def run(self) -> int:
        return self.secret()

d = Derived()
print(d.run())
