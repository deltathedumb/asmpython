# probes: PurePosixPath decomposes into parts
# expect:
# ('a', 'b', 'c.txt')
# c.txt
# .txt
import pathlib

p = pathlib.PurePosixPath("a/b/c.txt")
print(p.parts)
print(p.name)
print(p.suffix)
