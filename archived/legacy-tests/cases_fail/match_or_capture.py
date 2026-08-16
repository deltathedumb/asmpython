# expect-error: capture patterns are not allowed inside or-patterns
x = 1
match x:
    case 1 | n:
        print(n)
