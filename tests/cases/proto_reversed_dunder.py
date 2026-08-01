# probes: reversed() dispatches to __reversed__
# expect:
# ['last', 'first']
class Backwards:
    def __reversed__(self):
        return iter(["last", "first"])


print(list(reversed(Backwards())))
