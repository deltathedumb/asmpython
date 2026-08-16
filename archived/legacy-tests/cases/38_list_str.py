# expect:
# alice
# bob
# carol
# 3
# alice
# bob
# carol
# dave
# 4
# dave
# 3
names = ["alice", "bob", "carol"]
print(names[0])
print(names[1])
print(names[2])
print(len(names))

names.append("dave")
for n in names:
    print(n)
print(len(names))

last = names.pop()
print(last)
print(len(names))
