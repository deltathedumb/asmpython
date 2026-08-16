# expect:
# enter a
# using a
# exit a
# after a
# enter b
# using b
# inside b
# exit b
# after b
# enter c
# using c
# exit c
# no error
# enter c
# using c
# exit c
# caught boom

class Resource:
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        print("enter", self.name)
        return self

    def __exit__(self, exc_type, exc_value, tb):
        print("exit", self.name)

    def use(self):
        print("using", self.name)


with Resource("a") as r:
    r.use()

print("after a")

r2 = Resource("b")
with r2 as r3:
    r3.use()
    print("inside b")

print("after b")


def maybe_raise(n):
    with Resource("c") as r:
        r.use()
        if n == 1:
            raise ValueError("boom")
    return "no error"


try:
    print(maybe_raise(0))
except ValueError as e:
    print("caught", e)

try:
    print(maybe_raise(1))
except ValueError as e:
    print("caught", e)
