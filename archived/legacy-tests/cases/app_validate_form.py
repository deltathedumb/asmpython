# expect:
# [('age', 'too young'), ('email', 'required')]
def validate(form):
    errors = {}
    if not form.get('email'):
        errors['email'] = 'required'
    if form.get('age', 0) < 18:
        errors['age'] = 'too young'
    return errors
result = validate({'email': '', 'age': 15})
print(sorted(result.items()))
# asmpython (beta/3.14.0) MISMATCH: prints "[('age', 5368737821), ('email', 5368737808)]\n" (wrong).
