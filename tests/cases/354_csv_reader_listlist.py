# expect:
# name
# alice
# bob
# alice,30

import csv

lines: list[str] = ["name,age", "alice,30", "bob,25"]
rows: list[list[str]] = csv.reader(lines)
for row in rows:
    print(row[0])

out: list[str] = csv.writer_rows([["alice", "30"], ["bob", "25"]])
print(out[0])
