# probes: __exit__ receives the exception it is handling
# expect:
# ValueError
# details
class Inspect:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        print(exc_type.__name__)
        print(str(exc))
        return True


with Inspect():
    raise ValueError("details")
