# probes: a method is reachable through the class
# expect:
# hi
# hi
class Greeter:
    def greet(self):
        return "hi"


g = Greeter()
print(Greeter.greet(g))
print(g.greet())
