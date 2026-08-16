# expect:
# True True 1
import functools
@functools.total_ordering
class Temp:
    def __init__(self, deg):
        self.deg = deg
    def __eq__(self, o):
        return self.deg == o.deg
    def __lt__(self, o):
        return self.deg < o.deg
print(Temp(20) < Temp(30), Temp(30) >= Temp(20), sorted([Temp(3), Temp(1)], key=lambda t: t.deg)[0].deg)
# asmpython (beta/3.14.0) rejects at compile: unsupported expr Call (sorted key lambda body)
