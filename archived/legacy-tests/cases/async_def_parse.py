# expect:
# defined
async def f():
    return 5
print('defined')
# asmpython (beta/3.14.0) rejects at compile: [P001] unexpected token KEYWORD 'async'
