# probes: print falls back to __repr__
# expect:
# <Tagged>
class Tagged:
    def __repr__(self):
        return "<Tagged>"


print(Tagged())
