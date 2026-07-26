# expect:
# ended at: end
rooms = {'start': {'north': 'hall'}, 'hall': {'south': 'start', 'north': 'end'}, 'end': {}}
current = 'start'
path = ['north', 'north']
for direction in path:
    if direction in rooms[current]:
        current = rooms[current][direction]
print('ended at:', current)
# asmpython (beta/3.14.0) MISMATCH: prints 'ended at: 5368741911\n' (wrong).
