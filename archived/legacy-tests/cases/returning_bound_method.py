# expect:
# hi bob
class Greeter:
    def __init__(self, name):
        self.name = name
    def greet(self):
        return 'hi ' + self.name
g = Greeter('bob')
method = g.greet
print(method())
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
