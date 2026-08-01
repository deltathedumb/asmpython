# tier: spec
# ref: reference/compound_stmts.html#the-with-statement
# expect:
# 1 2
# 3 4
class CM:
    def __init__(self, n):
        self.n = n
    def __enter__(self):
        return self.n
    def __exit__(self, *a):
        return False

with CM(1) as a, CM(2) as b:
    print(a, b)

with (CM(3) as c, CM(4) as d):
    print(c, d)
