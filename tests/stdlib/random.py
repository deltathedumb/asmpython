# COVERAGE: the module-level bound functions over a shared `Random` --
# `seed`, `random`, `getrandbits`, `randrange` (unit and non-unit step),
# `randint`, `choice`, `choices` (weights and cum_weights), `shuffle`,
# `sample`, `uniform`, `gauss`, `getstate`/`setstate`. NOT covered:
# `triangular`, `normalvariate`, `lognormvariate`, `expovariate`,
# `vonmisesvariate`, `gammavariate`, `betavariate`, `paretovariate`,
# `weibullvariate`, `binomialvariate`, `randbytes`, `SystemRandom`,
# seeding by anything but an int.
#
# CPython's random.Random is the Mersenne Twister with a specific seeding
# scheme (init_by_array over a seed's 32-bit words, not init_genrand
# directly). Everything below is deterministic given the seed, so CPython
# and uasm MUST print the identical sequence -- that is the entire
# point of reimplementing MT19937 by hand rather than approximating it.
import random

for s in (0, 42, 12345):
    random.seed(s)
    print("seed", s)

    print([random.random() for _ in range(5)])
    print([random.randint(1, 100) for _ in range(5)])
    # Non-unit step exercises randrange's second arithmetic path, not just
    # the width > 0 fast path.
    print([random.randrange(10, 50, 3) for _ in range(5)])
    print([random.randrange(7) for _ in range(5)])

    pop = list(range(20))
    print([random.choice(pop) for _ in range(5)])

    xs = list(range(10))
    ys = xs[:]
    random.shuffle(ys)
    print(xs, ys)

    # n well under the small-pool threshold (setsize=21 for k<=5), so the
    # branch taken cannot be sensitive to the setsize formula's rounding.
    print(random.sample(range(30), 5))
    print(random.sample(["a", "b", "c", "d", "e", "f", "g", "h"], 4))

    print([random.getrandbits(n) for n in (1, 7, 8, 16, 31, 32, 33, 64, 100)])

    letters = ["a", "b", "c", "d"]
    print(random.choices(letters, weights=[10, 20, 30, 40], k=8))
    print(random.choices(letters, cum_weights=[10, 30, 60, 100], k=8))
    print(random.choices(letters, k=8))

    print([round(random.uniform(1.5, 9.5), 9) for _ in range(5)])
    print([round(random.gauss(0.0, 1.0), 9) for _ in range(5)])
    print([round(random.gauss(10.0, 2.5), 9) for _ in range(5)])

    # getstate/setstate: rewind and replay must reproduce the same draws.
    state = random.getstate()
    first = [random.random() for _ in range(4)]
    random.setstate(state)
    second = [random.random() for _ in range(4)]
    print(first == second, first)

print("done")
