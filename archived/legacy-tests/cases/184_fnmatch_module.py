# expect:
# 1
# 0
# 1
# 3

from fnmatch import fnmatch, fnfilter as filter

print(fnmatch("hello.py", "*.py"))
print(fnmatch("hello.txt", "*.py"))
print(fnmatch("test_foo.py", "test_*.py"))

names = ["foo.py", "bar.py", "baz.txt", "qux.py"]
matches = filter(names, "*.py")
print(len(matches))
