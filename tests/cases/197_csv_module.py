# expect:
# alice
# 30
# bob
# 25

from csv import reader, Row

lines: list[str] = ["name,age", "alice,30", "bob,25"]
rows: list[Row] = reader(lines)
i: int = 1
while i < len(rows):
    row: Row = rows[i]
    print(row[0])
    print(row[1])
    i = i + 1
