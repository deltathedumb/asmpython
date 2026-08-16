# probes: an Enum member exposes name and value
# expect:
# RED
# 1
# GREEN
import enum


class Color(enum.Enum):
    RED = 1
    GREEN = 2


print(Color.RED.name)
print(Color.RED.value)
print(Color(2).name)
