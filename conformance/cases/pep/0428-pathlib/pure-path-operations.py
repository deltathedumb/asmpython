# tier: spec
# ref: library/pathlib.html
# expect:
# c.txt c .txt
# /a/b
# ('/', 'a', 'b', 'c.txt')
# a/b
# /a/b/c.md
# True
from pathlib import PurePosixPath

p = PurePosixPath("/a/b/c.txt")
print(p.name, p.stem, p.suffix)
print(p.parent)
print(p.parts)
print(PurePosixPath("a") / "b")
print(p.with_suffix(".md"))
print(p.is_absolute())
