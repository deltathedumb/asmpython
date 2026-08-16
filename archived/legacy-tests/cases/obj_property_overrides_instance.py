# probes: a property wins over an instance dict entry
# expect:
# assignment refused
# from-property
class Thing:
    @property
    def name(self):
        return "from-property"


t = Thing()
try:
    t.name = "from-instance"
    print("assignment allowed")
except AttributeError:
    print("assignment refused")
print(t.name)
