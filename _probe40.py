# probe40: contextmanager, with statement, file I/O
import sys

# sys.argv
print(sys.argv[0])  # script name or similar

# sys.exit (probably can't test cleanly)

# with statement (context manager)
# likely only supported for file objects?
# test open/with if available

# write to stderr
print("error", file=sys.stderr)

# multiple print args with sep
print(1, 2, 3, sep="-")       # 1-2-3
print("a", "b", sep=", ")     # a, b
print(1, 2, 3, sep="", end="!\n")  # 123!
