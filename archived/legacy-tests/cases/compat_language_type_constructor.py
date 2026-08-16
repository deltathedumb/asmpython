# guards: language_compat_fixes
# expect:
# sample
# Sample
class Sample:
    def __init__(self):
        self.tag = "sample"

    def show(self):
        return self.tag


proto = Sample()
clone = type(proto)()
print(clone.show())
print(type(proto).__name__)
