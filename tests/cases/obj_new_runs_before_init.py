# probes: __new__ runs before __init__
# expect:
# new
# init
class Ordered:
    def __new__(cls):
        print("new")
        return super().__new__(cls)

    def __init__(self):
        print("init")


Ordered()
