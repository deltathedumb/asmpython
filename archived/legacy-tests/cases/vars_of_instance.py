# expect:
# [('a', 1), ('b', 2)]
class C:
    def __init__(self):
        self.a = 1
        self.b = 2
print(sorted(vars(C()).items()))
# asmpython (beta/3.14.0) rejects at compile: [E149] vars() is not supported: it requires a Python interpreter and cannot be compiled to native code
