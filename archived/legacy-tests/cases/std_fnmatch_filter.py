# probes: fnmatch.filter selects matching names
# expect:
# ['a.py', 'c.py']
import fnmatch

print(fnmatch.filter(["a.py", "b.txt", "c.py"], "*.py"))
