# expect:
# abc
from functools import reduce
words = ['a', 'b', 'c']
print(reduce(lambda acc, w: acc + w, words, ''))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
