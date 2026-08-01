# tier: spec
# ref: reference/compound_stmts.html#the-with-statement
# expect:
# enter
# value
# exit None
# enter
# exit ValueError
# caught
class CM:
    def __enter__(self):
        print("enter")
        return "value"
    def __exit__(self, *a):
        print("exit", a[0].__name__ if a[0] else None)
        return False

with CM() as v:
    print(v)

try:
    with CM():
        raise ValueError("x")
except ValueError:
    print("caught")
