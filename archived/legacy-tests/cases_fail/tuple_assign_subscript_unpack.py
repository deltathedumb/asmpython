# expect-error: tuple assign with subscript/attribute targets requires the parallel form
nums = [1, 2, 3]
t = (1, 2)
nums[0], nums[1] = t
