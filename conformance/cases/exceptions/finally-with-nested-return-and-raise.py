# tier: spec
# ref: reference/compound_stmts.html#the-try-statement
# expect:
# returned caught
# ['ok', 'raise']
log = []

def f(mode):
    try:
        if mode == "raise":
            raise ValueError("x")
        return "returned"
    except ValueError:
        return "caught"
    finally:
        log.append(mode)

print(f("ok"), f("raise"))
print(log)
