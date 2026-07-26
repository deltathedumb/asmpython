# expect:
# [(2, 'a'), (1, 'b')]
from operator import itemgetter
data = [(1, 'b'), (2, 'a')]
print(sorted(data, key=itemgetter(1)))
# asmpython (beta/3.14.0) rejects at compile: [E135] key= must be a lambda literal or a name bound to a lambda
