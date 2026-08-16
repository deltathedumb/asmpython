# expect:
# 1
try:
    from collections import OrderedDict as OD
except ImportError:
    OD = dict
d = OD()
d['x'] = 1
print(d['x'])
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
