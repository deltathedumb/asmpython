# tier: spec
# ref: reference/compound_stmts.html#the-with-statement
# expect:
# RuntimeError from-exit
# RuntimeError ValueError
class CM:
    def __enter__(self):
        return self
    def __exit__(self, *a):
        raise RuntimeError("from-exit")

try:
    with CM():
        pass
except RuntimeError as e:
    print("RuntimeError", e)

try:
    with CM():
        raise ValueError("from-body")
except RuntimeError as e:
    print("RuntimeError", type(e.__context__).__name__)
