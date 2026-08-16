# expect:
# ['apply', 'apple', 'ape']
import difflib
print(difflib.get_close_matches('appel', ['apple', 'ape', 'apply']))
# asmpython (beta/3.14.0) MISMATCH: prints '0\n' (wrong).
