# expect:
# ['name required', 'age invalid']
def validate(data):
    errors = []
    if 'name' not in data:
        errors.append('name required')
    if data.get('age', 0) < 0:
        errors.append('age invalid')
    return errors
print(validate({'age': -5}))
# asmpython (beta/3.14.0) MISMATCH: prints '[5368737807]\n' (wrong).
