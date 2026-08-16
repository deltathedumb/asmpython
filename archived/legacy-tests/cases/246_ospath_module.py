# expect:
# a/b
# c.py
# a/b
# 1

import ospath

print(ospath.join("a", "b"))
print(ospath.basename("a/b/c.py"))
print(ospath.dirname("a/b/c.py"))
print(ospath.exists("."))
