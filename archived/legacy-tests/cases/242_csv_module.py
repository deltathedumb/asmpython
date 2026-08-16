# expect:
# 3
# name
# age
# alice
# alice,30,NYC

import csv

lines: list[str] = ["name,age,city", "alice,30,NYC", "bob,25,LA"]
rows = csv.reader(lines)
print(len(rows))
r0 = rows[0]
print(r0[0])
print(r0[1])
r1 = rows[1]
print(r1[0])

line: str = csv.writer_row(["alice", "30", "NYC"])
print(line)
