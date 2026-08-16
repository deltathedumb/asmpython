# expect:
# HI
from operator import methodcaller
upper = methodcaller('upper')
print(upper('hi'))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
