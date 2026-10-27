Authored positive control: the final price is omitted because the implementation
sums prices[:-1]. For [100, 200], the actual result is 100 rather than 300.
An empty list returns zero; claiming that it raises IndexError is unsupported.
This reference is exposed development material, not an independent judge result.
