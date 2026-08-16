# expect:
# alice
# bob
# alice
# 1
# 3
# 4
people = [{"n": "alice"}, {"n": "bob"}]
for p in people:
    print(p["n"])
print(people[0]["n"])
grid = [[1, 2], [3, 4]]
for row in grid:
    print(row[0])
print(grid[1][1])
