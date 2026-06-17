# expect:
# a
# c
# 3
# 9
# hello
# 30

def get_words() -> list[list[str]]:
    r1: list[str] = ["a", "b"]
    r2: list[str] = ["c", "d"]
    result: list[list[str]] = []
    result.append(r1)
    result.append(r2)
    return result

def get_matrix() -> list[list[int]]:
    r1: list[int] = [1, 2, 3]
    r2: list[int] = [4, 5, 6]
    m: list[list[int]] = []
    m.append(r1)
    m.append(r2)
    return m

def get_records() -> list[dict[str, int]]:
    r1: dict[str, int] = {}
    r1["name"] = 0
    r1["age"] = 30
    r2: dict[str, int] = {}
    r2["name"] = 0
    r2["age"] = 25
    result: list[dict[str, int]] = []
    result.append(r1)
    result.append(r2)
    return result

words = get_words()
for row in words:
    print(row[0])

m = get_matrix()
for row in m:
    print(row[0] + row[1])

records = get_records()
rec: dict[str, int] = records[0]
print("hello")
print(rec["age"])
