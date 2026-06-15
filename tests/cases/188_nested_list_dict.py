# expect:
# hello
# world
# 2

rows: list[dict[str, str]] = []
d1: dict[str, str] = {}
d1["greeting"] = "hello"
d1["farewell"] = "world"
rows.append(d1)

for row in rows:
    print(row["greeting"])
    print(row["farewell"])

print(len(rows))
