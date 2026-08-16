# expect:
# 2
grid = [[1, 1, 0], [0, 1, 0], [0, 0, 1]]
def count():
    seen = set()
    def visit(r, c):
        if r < 0 or c < 0 or r >= 3 or c >= 3:
            return
        if (r, c) in seen or grid[r][c] == 0:
            return
        seen.add((r, c))
        visit(r + 1, c)
        visit(r - 1, c)
        visit(r, c + 1)
        visit(r, c - 1)
    n = 0
    for r in range(3):
        for c in range(3):
            if grid[r][c] == 1 and (r, c) not in seen:
                n += 1
                visit(r, c)
    return n
print(count())
# asmpython (beta/3.14.0) rejects at compile: [E113] int has no method 'add'
