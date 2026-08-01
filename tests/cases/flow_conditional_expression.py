# probes: a conditional expression picks one branch
# expect:
# big
# small
def label(n):
    return "big" if n > 10 else "small"


print(label(50))
print(label(1))
