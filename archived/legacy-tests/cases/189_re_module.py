# expect:
# 1
# hello
# 0
# 1

import re

m = re.match("hello", "hello world")
print(1 if m is not None else 0)
if m is not None:
    print(m.group(0))

m2 = re.match("world", "hello world")
print(1 if m2 is not None else 0)

m3 = re.search("world", "hello world")
print(1 if m3 is not None else 0)
