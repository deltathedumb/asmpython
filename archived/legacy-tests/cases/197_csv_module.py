# expect:
# alice
# 30
# bob
# 25

from csv import reader

lines: list[str] = ["name,age", "alice,30", "bob,25"]
rows: list[list[str]] = reader(lines)
i: int = 1
while i < len(rows):
    row: list[str] = rows[i]
    print(row[0])
    print(row[1])
    i = i + 1
