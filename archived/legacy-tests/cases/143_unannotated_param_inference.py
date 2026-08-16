# expect:
# world
# hello there
# returned there
# got item

class Box:
    def __init__(self, label):
        self.label = label

    def show(self):
        print(self.label)


def greet(msg):
    print("hello", msg)
    return msg


class Wrapper:
    def __init__(self, value):
        self.value = value

    def tag(self, prefix):
        print(prefix, self.value)


b = Box("world")
b.show()

r = greet("there")
print("returned", r)

w = Wrapper("item")
w.tag("got")
