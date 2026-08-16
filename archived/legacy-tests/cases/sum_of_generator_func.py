# expect:
# 30
def squares(n):
    for i in range(n):
        yield i * i
print(sum(squares(5)))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
