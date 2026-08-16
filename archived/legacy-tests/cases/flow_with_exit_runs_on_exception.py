# probes: __exit__ runs when the body raises
# expect:
# exit ran
# propagated
class Trace:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        print("exit ran")
        return False


try:
    with Trace():
        raise ValueError("boom")
except ValueError:
    print("propagated")
