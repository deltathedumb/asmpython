# expect-error: Foo has no method 'missing'
class Foo:
    def __init__(self):
        self.x = 1

f = Foo()
f.missing()
