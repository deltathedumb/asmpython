# expect:
# 1 GREEN
from enum import Enum
class Color(Enum):
    RED = 1
    GREEN = 2
print(Color.RED.value, Color.GREEN.name)
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
