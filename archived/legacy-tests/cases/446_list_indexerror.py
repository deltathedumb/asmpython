# expect:
# caught index error
# caught neg oob
# ok: 3
# ok: 1

lst = [1, 2, 3]

try:
    x = lst[10]
except IndexError:
    print("caught index error")

try:
    y = lst[-10]
except IndexError:
    print("caught neg oob")

try:
    z = lst[2]
    print("ok:", z)
except IndexError:
    print("should not reach")

try:
    w = lst[-3]
    print("ok:", w)
except IndexError:
    print("should not reach")
