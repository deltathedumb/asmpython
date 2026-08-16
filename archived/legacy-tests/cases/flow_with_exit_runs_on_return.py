# probes: __exit__ runs on the return path
# expect:
# exit ran
# returned
class Trace:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        print("exit ran")
        return False


def f():
    with Trace():
        return "returned"


print(f())
