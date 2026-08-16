# expect:
# a b
class M:
    def __init__(self, n):
        self.n = n
    def __enter__(self):
        return self.n
    def __exit__(self, *a):
        pass
with (M('a') as a, M('b') as b):
    print(a, b)
# asmpython (beta/3.14.0) rejects at compile: [P002] expected OP ')', got KEYWORD 'as'
