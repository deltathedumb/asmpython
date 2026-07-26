# expect:
# winner: alice with 3 votes
votes = ['alice', 'bob', 'alice', 'carol', 'alice', 'bob']
tally = {}
for v in votes:
    tally[v] = tally.get(v, 0) + 1
winner = max(tally, key=lambda k: tally[k])
print('winner:', winner, 'with', tally[winner], 'votes')
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
