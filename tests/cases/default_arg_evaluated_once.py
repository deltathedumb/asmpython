# expect:
# [1]
# [1, 2]
def append_to(x, lst=[]):
    lst.append(x)
    return lst
print(append_to(1))
print(append_to(2))
# asmpython (beta/3.14.0) MISMATCH: prints '9671536\n9671632\n' (wrong).
