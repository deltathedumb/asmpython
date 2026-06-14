# expect:
# 3
# name
# age
# city
# 3
# Alice
# 30
# New York
# ---
# a,"b,c","d""e"
# ---
# name,age,city
# Alice,30,New York
# ---
# Alice New York
# 0

from csv import reader, writer_row, DictReader

lines = [
    "name,age,city",
    "Alice,30,\"New York\"",
]

rows = reader(lines)
for row in rows:
    print(len(row.fields))
    for f in row.fields:
        print(f)

print("---")
print(writer_row(["a", "b,c", "d\"e"]))

print("---")
print(writer_row(rows[0].fields))
print(writer_row(rows[1].fields))

print("---")
dr = DictReader(lines)
data_row = dr.rows[0]
print(dr.get(data_row, "name"), dr.get(data_row, "city"))
print(len(reader([""])[0].fields))
