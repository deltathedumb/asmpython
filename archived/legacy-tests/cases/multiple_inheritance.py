# expect:
# a b
class A:
    def a(self):
        return 'a'
class B:
    def b(self):
        return 'b'
class C(A, B):
    pass
c = C()
print(c.a(), c.b())
# asmpython (beta/3.14.0) rejects at compile: [E113] C has no method 'b'
