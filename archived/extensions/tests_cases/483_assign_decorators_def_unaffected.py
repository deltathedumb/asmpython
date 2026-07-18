# ext: assign_decorators
# expect:
# 42

class Foo:
    @staticmethod
    def bar() -> int:
        return 42

print(Foo.bar())
