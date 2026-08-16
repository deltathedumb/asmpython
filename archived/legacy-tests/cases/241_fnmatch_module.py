# expect:
# 1
# 0
# 1
# 2

import fnmatch

print(fnmatch.fnmatch("hello.py", "*.py"))
print(fnmatch.fnmatch("hello.txt", "*.py"))
print(fnmatch.fnmatch("test_foo.py", "test_*.py"))

files: list[str] = ["a.py", "b.txt", "c.py", "d.md"]
matched: list[str] = fnmatch.fnfilter(files, "*.py")
print(len(matched))
