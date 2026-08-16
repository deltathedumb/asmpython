# probes: f(*xs) spreads elements into parameters (str elements)
# expect:
# ['aa', 'bb', 'cc']
def take3(a, b, c):
    return [a, b, c]


xs = ["aa", "bb", "cc"]
print(take3(*xs))
