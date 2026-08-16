# probes: an unannotated str return stays a str
# expect:
# positive
# POSITIVE
# 8
def label(n):
    if n > 0:
        return "positive"
    return "non-positive"


v = label(1)
print(v)
print(v.upper())
print(len(v))
