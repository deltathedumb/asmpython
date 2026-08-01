# probes: nested managers exit innermost first
# expect:
# body
# exit inner
# exit outer
class Named:
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        print("exit " + self.name)
        return False


with Named("outer"):
    with Named("inner"):
        print("body")
