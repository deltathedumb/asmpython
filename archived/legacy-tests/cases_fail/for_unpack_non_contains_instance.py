# expect-error: does not define __contains__

class Foo:
    def __init__(self) -> None:
        self.x: int = 0

f: Foo = Foo()
r: int = 1 if 1 in f else 0
