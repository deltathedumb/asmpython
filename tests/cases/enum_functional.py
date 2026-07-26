# expect:
# 1 3
from enum import Enum
Color = Enum('Color', ['RED', 'GREEN', 'BLUE'])
print(Color.RED.value, Color.BLUE.value)
# asmpython (beta/3.14.0) rejects at compile: [E021] Enum() takes 1 argument(s), got 2
