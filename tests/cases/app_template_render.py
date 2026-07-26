# expect:
# Hello Bob, you are 30
def render(template, context):
    result = template
    for key, val in context.items():
        result = result.replace('{' + key + '}', str(val))
    return result
print(render('Hello {name}, you are {age}', {'name': 'Bob', 'age': 30}))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
