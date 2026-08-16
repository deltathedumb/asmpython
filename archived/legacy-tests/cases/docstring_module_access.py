# expect:
# Returns 1
def documented():
    'Returns 1'
    return 1
print(documented.__doc__)
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
