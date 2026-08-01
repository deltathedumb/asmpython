# tier: spec
# ref: reference/compound_stmts.html#the-with-statement
# expect:
# enter 1
# enter 2
# body 1 2
# exit 2
# exit 1
class CM:
    def __init__(self, n):
        self.n = n
    def __enter__(self):
        print("enter", self.n)
        return self.n
    def __exit__(self, *a):
        print("exit", self.n)
        return False

with CM(1) as a, CM(2) as b:
    print("body", a, b)
