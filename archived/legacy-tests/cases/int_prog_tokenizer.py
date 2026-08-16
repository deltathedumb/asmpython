# expect:
# [12, '+', 34, '*', 5]
def tokenize(expr):
    tokens = []
    num = ''
    for ch in expr:
        if ch.isdigit():
            num += ch
        else:
            if num:
                tokens.append(int(num))
                num = ''
            if ch in '+-*/':
                tokens.append(ch)
    if num:
        tokens.append(int(num))
    return tokens
print(tokenize('12+34*5'))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
