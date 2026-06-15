# expect:
# 1
# 1
# 0
# 1
# 0
# 2

import fnmatch

print(fnmatch.fnmatch("hello.py", "*.py"))
print(fnmatch.fnmatch("test_foo.py", "test_*.py"))
print(fnmatch.fnmatch("hello.txt", "*.py"))
print(fnmatch.fnmatch("data.csv", "*.csv"))
print(fnmatch.fnmatchcase("Hello.PY", "*.py"))

names: list[str] = ["a.py", "b.txt", "c.py", "d.json"]
result: list[str] = fnmatch.fnfilter(names, "*.py")
print(len(result))
