# expect:
# ok
# 2
# True
# False
# debug
# verbose

def accept_all(**kwargs):
    # Function receives excess keyword arguments — no error, no crash
    print("ok")

accept_all(a=1, b=2, c=3)

def count_keys(**kwargs):
    print(len(kwargs))

count_keys(x=10, y=20)

def has_key(**kwargs):
    print("debug" in kwargs)
    print("missing" in kwargs)

has_key(debug=True, verbose=False)

def show_keys(**kwargs):
    for k in kwargs:
        print(k)

show_keys(debug=1, verbose=0)
