# expect:
# ['fig', 'kiwi', 'apple', 'banana']
# ['kiwi', 'fig', 'banana', 'apple']
# [3, 2, 1]
# fig
# banana
# apple
# kiwi
# [9, 5, 2, 1]
# ['carlos', 'bob', 'al']

words = ["banana", "kiwi", "fig", "apple"]
print(sorted(words, key=lambda w: len(w)))
print(sorted(words, reverse=True))
print(sorted([3, 1, 2], reverse=True))
print(min(words, key=lambda w: len(w)))
print(max(words, key=lambda w: len(w)))
print(min(words))
print(max(words))

nums = [5, 2, 9, 1]
nums.sort(key=lambda x: -x)
print(nums)

names = ["bob", "al", "carlos"]
names.sort(key=lambda s: len(s), reverse=True)
print(names)
