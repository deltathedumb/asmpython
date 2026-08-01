# probes: an Enum iterates in declaration order
# expect:
# ['RED', 'GREEN', 'BLUE']
import enum


class Color(enum.Enum):
    RED = 1
    GREEN = 2
    BLUE = 3


print([member.name for member in Color])
