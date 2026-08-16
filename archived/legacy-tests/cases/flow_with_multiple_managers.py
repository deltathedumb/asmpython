# probes: one with statement can hold several managers
# expect:
# enter a
# enter b
# ab
# exit b
# exit a
class Named:
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        print("enter " + self.name)
        return self.name

    def __exit__(self, exc_type, exc, tb):
        print("exit " + self.name)
        return False


with Named("a") as first, Named("b") as second:
    print(first + second)
