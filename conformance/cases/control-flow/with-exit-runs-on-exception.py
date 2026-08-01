# tier: spec
# ref: reference/compound_stmts.html#the-with-statement
# expect:
# [('enter', 1), ('enter', 2), ('exit', 2, 'ValueError'), ('exit', 1, 'ValueError')]
log = []

class CM:
    def __init__(self, n, swallow=False):
        self.n = n
        self.swallow = swallow
    def __enter__(self):
        log.append(("enter", self.n))
        return self
    def __exit__(self, et, ev, tb):
        log.append(("exit", self.n, et.__name__ if et else None))
        return self.swallow

with CM(1, swallow=True):
    with CM(2):
        raise ValueError("x")
print(log)
