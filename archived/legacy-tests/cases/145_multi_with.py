# expect:
# enter a
# enter b
# using a
# using b
# exit b
# exit a
# after
# enter c
# enter d
# using c
# using d
# exit d
# exit c
# no error
# enter c
# enter d
# using c
# using d
# exit d
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


with Resource("a") as a, Resource("b") as b:
    a.use()
    b.use()

print("after")


def maybe_raise(n):
    with Resource("c") as c, Resource("d") as d:
        c.use()
        d.use()
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
