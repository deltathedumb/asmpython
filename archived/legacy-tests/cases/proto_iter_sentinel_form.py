# probes: iter(callable, sentinel) stops at the sentinel
# expect:
# [1, 2, 3]
values = [1, 2, 3, 0, 4]
position = []


def take():
    index = len(position)
    position.append(1)
    return values[index]


print(list(iter(take, 0)))
