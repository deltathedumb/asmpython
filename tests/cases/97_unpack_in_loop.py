# expect:
# alice 90
# bob 85
# ('alice', 90)
# alice
data = [("alice", 90), ("bob", 85)]
for name, score in data:
    print(name, score)
print(data[0])
print(data[0][0])
