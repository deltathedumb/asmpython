# probes: a bound method remembers its receiver
# expect:
# hi ada
# ada
class Greeter:
    def __init__(self, name):
        self.name = name

    def greet(self):
        return "hi " + self.name


bound = Greeter("ada").greet
print(bound())
print(bound.__self__.name)
