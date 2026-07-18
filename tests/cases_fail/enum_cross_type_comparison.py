# ext: enum
# expect-error: different enum types

enum Color:
    RED
    GREEN

enum Direction:
    NORTH
    SOUTH

if Color.RED == Direction.NORTH:
    print("bug")
