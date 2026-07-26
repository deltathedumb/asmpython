# expect:
# 4 4
from collections import Counter
c = Counter('mississippi')
print(c['s'], c['i'])
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
