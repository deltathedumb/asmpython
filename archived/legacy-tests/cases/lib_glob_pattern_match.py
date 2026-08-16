# expect:
# ['a.py', 'c.py']
import fnmatch
files = ['a.py', 'b.txt', 'c.py']
print([f for f in files if fnmatch.fnmatch(f, '*.py')])
# asmpython (beta/3.14.0) MISMATCH: prints '[]\n' (wrong).
