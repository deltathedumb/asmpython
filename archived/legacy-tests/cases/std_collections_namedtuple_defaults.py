# probes: namedtuple accepts defaults=
# expect:
# Point(x=1, y=0)
import collections

Point = collections.namedtuple("Point", "x y", defaults=(0,))
print(Point(1))
