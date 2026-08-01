# probes: in dispatches to __contains__
# expect:
# True
# False
# True
class Evens:
    def __contains__(self, value):
        return value % 2 == 0


e = Evens()
print(4 in e)
print(5 in e)
print(5 not in e)
