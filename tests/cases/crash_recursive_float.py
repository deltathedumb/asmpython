# expect:
# 15.0
def sum_halves(n):
    if n < 1:
        return 0.0
    return n + sum_halves(n / 2)
print(round(sum_halves(8), 1))
# asmpython (beta/3.14.0) runtime failure: exit 0xc00000fd
