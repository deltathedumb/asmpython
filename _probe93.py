# probe93: complex nested data structures
# List of dicts
people = [
    {"name": "Alice", "age": 30, "city": "NYC"},
    {"name": "Bob", "age": 25, "city": "LA"},
    {"name": "Charlie", "age": 35, "city": "NYC"},
]

# Filter by city
nyc_people = [p for p in people if p["city"] == "NYC"]
print(len(nyc_people))      # 2
print(nyc_people[0]["name"]) # Alice
print(nyc_people[1]["name"]) # Charlie

# Sort by age
by_age = sorted(people, key=lambda p: p["age"])
print(by_age[0]["name"])   # Bob
print(by_age[1]["name"])   # Alice
print(by_age[2]["name"])   # Charlie

# Group by city (manual groupby)
groups: dict = {}
for p in people:
    city = p["city"]
    if city not in groups:
        groups[city] = []
    groups[city].append(p["name"])

print(len(groups["NYC"]))   # 2
print(len(groups["LA"]))    # 1
print(groups["LA"][0])      # Bob

# Dict of lists
registry: dict = {}
for p in people:
    age_key = str(p["age"] // 10 * 10)  # decade
    if age_key not in registry:
        registry[age_key] = []
    registry[age_key].append(p["name"])

print(len(registry["30"]))   # 2 (30 and 35 -> decade 30)
print(len(registry["20"]))   # 1 (25 -> decade 20)
