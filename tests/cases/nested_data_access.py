# expect:
# a 25
data = {'users': [{'name': 'a', 'age': 30}, {'name': 'b', 'age': 25}]}
print(data['users'][0]['name'], data['users'][1]['age'])
# asmpython (beta/3.14.0) MISMATCH: prints '5368750091 25\n' (wrong).
