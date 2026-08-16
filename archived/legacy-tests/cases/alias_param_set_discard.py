# probes: set discard through a parameter
# expect:
# 1
# [2]
def mutate(s):
    s.discard(1)


a = {1, 2}
mutate(a)
print(len(a))
print(sorted(a))
