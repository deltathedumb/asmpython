# expect:
# [3, 4, 5, 1, 2]
def rotate(lst, k):
    k = k % len(lst)
    return lst[k:] + lst[:k]
print(rotate([1, 2, 3, 4, 5], 2))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
