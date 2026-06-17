# expect:
# True
# True
# True
# \
# ;
# .
# ..
# .
# nul

import os
from os.path import join, basename, dirname, exists

# getcwd() returns a non-empty string
cwd = os.getcwd()
print(len(cwd) > 0)

# os.path.join
print(join("a", "b") == "a/b")

# os.path.basename / dirname
print(basename("/foo/bar.txt") == "bar.txt")

# platform constants
print(os.sep)
print(os.pathsep)
print(os.curdir)
print(os.pardir)
print(os.extsep)
print(os.devnull)
