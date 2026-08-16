# expect:
# [1, 2, 3]
from functools import cmp_to_key
print(sorted([3, 1, 2], key=cmp_to_key(lambda a, b: a - b)))
# asmpython (beta/3.14.0) rejects at compile: [E135] key= must be a lambda literal or a name bound to a lambda
