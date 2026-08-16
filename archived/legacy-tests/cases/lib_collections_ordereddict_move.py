# expect:
# ['b', 'a']
from collections import OrderedDict
d = OrderedDict([('a', 1), ('b', 2)])
d.move_to_end('a')
print(list(d))
# asmpython (beta/3.14.0) rejects at compile: [E021] OrderedDict() takes 0 argument(s), got 1
