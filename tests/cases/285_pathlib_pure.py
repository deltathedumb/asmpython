# expect:
# foo/bar
# foo/bar/baz.txt
# baz.txt
# .txt

from pathlib import PurePath, PurePosixPath, PureWindowsPath

p = PurePath("foo/bar")
print(str(p))

p2 = p / "baz.txt"
print(str(p2))
print(p2.name())
print(p2.suffix())
