# expect:
# [2, 3]
def find_dups(lst):
    seen = set()
    dups = set()
    for x in lst:
        if x in seen:
            dups.add(x)
        seen.add(x)
    return sorted(dups)
print(find_dups([1, 2, 3, 2, 4, 3, 5]))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
