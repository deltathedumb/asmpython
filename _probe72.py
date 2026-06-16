# probe72: closures, higher-order functions, decorators
def make_adder(n: int):
    def add(x: int) -> int:
        return x + n
    return add

add5 = make_adder(5)
add10 = make_adder(10)
print(add5(3))    # 8
print(add10(3))   # 13
print(add5(0))    # 5

# Function returning function
def compose(f, g):
    def h(x: int) -> int:
        return f(g(x))
    return h

def double(x: int) -> int:
    return x * 2

def inc(x: int) -> int:
    return x + 1

double_then_inc = compose(inc, double)
inc_then_double = compose(double, inc)
print(double_then_inc(3))   # 7  (3*2+1)
print(inc_then_double(3))   # 8  ((3+1)*2)

# Decorator
def logged(f):
    def wrapper(x: int) -> int:
        result: int = f(x)
        print("called with", x, "got", result)
        return result
    return wrapper

@logged
def square(x: int) -> int:
    return x * x

square(4)    # called with 4 got 16
square(5)    # called with 5 got 25
