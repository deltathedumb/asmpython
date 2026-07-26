# expect:
# 0.67
import difflib
print(round(difflib.SequenceMatcher(None, 'abc', 'abd').ratio(), 2))
# asmpython (beta/3.14.0) MISMATCH: prints '100.0\n' (wrong).
