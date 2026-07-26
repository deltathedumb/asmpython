# expect:
# ['apple', 'pear']
items = [('fruit', 'apple'), ('veg', 'carrot'), ('fruit', 'pear')]
groups = {}
for cat, name in items:
    groups.setdefault(cat, []).append(name)
print(sorted(groups.get('fruit', [])))
# asmpython (beta/3.14.0) rejects at compile: [E113] int has no method 'append'
