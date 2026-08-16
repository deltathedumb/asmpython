# probes: f(*xs) spreads elements into parameters (int elements)
# expect:
# [10, 20, 30]
def take3(a, b, c):
    return [a, b, c]


xs = [10, 20, 30]
print(take3(*xs))
