# probes: basename/dirname split a posix-style path
# expect:
# c.txt
# a/b
import os.path

print(os.path.basename("a/b/c.txt"))
print(os.path.dirname("a/b/c.txt"))
