# expect:
# inner
class Outer:
    class Inner:
        def hello(self):
            return 'inner'
o = Outer.Inner()
print(o.hello())
# asmpython (beta/3.14.0) rejects at compile: [E113] Outer has no method 'Inner'
