# expect:
# 14
def evaluate(tokens):
    stack = []
    for tok in tokens:
        if tok in ('+', '*'):
            b = stack.pop()
            a = stack.pop()
            stack.append(a + b if tok == '+' else a * b)
        else:
            stack.append(tok)
    return stack[0]
print(evaluate([2, 3, 4, '*', '+']))
# asmpython (beta/3.14.0) rejects at compile: [E051] mixed list element types (int and str); mixed-type lists need a tagged-value runtime, not yet implemented
