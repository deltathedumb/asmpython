# expect:
# [1, 9, 4]
a = [1, 2, 3, 4]
a[1:3] = [9]
print(a)
# slice assignment does not resize; asmpython prints [1, 9, 3, 4] not [1, 9, 4].
