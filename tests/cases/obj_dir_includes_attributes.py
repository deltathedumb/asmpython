# probes: dir() lists declared members
# expect:
# True
# True
# False
class Widget:
    field = 1

    def method(self):
        return 2


names = dir(Widget())
print("field" in names)
print("method" in names)
print("absent" in names)
