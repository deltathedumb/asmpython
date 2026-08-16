# probes: difflib.ndiff marks per-line differences
# expect:
#   a
# - b
# + c
import difflib

for line in difflib.ndiff(["a", "b"], ["a", "c"]):
    print(line)
