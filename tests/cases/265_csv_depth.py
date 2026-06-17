# expect:
# 3
# name
# age
# alice
# 2
# name,age
# alice,30

import csv

lines: list[str] = ["name,age", "alice,30", "bob,25"]
rows: list[list[str]] = csv.reader(lines)
print(len(rows))
r0: list[str] = rows[0]
print(r0[0])
print(r0[1])
r1: list[str] = rows[1]
print(r1[0])
print(len(r1))

row_data: list[str] = ["name", "age"]
header_line: str = csv.writer_row(row_data)
print(header_line)

row2: list[str] = ["alice", "30"]
csv_line: str = csv.writer_row(row2)
print(csv_line)
