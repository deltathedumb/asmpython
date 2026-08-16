# expect:
# True
# True

import random

# choice from a single-element list always returns that element
items: list = [42]
v: int = random.choice(items)
print(v == 42)

# shuffle a list then verify len unchanged
data: list = [1, 2, 3, 4, 5]
random.shuffle(data)
print(len(data) == 5)
