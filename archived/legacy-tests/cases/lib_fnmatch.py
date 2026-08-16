# expect:
# True False
import fnmatch
print(fnmatch.fnmatch('file.txt', '*.txt'), fnmatch.fnmatch('file.py', '*.txt'))
# asmpython (beta/3.14.0) MISMATCH: prints '1 0\n' (wrong).
