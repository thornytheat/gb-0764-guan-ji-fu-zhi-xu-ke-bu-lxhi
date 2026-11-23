def normalize(text):
    intervals = []
    for expression in text.split(';'):
        numbers = list(map(int, expression.split('-')))
        intervals.append((numbers[0], numbers[-1]+1))
    merged = []
    for start,end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end,merged[-1][1])
        else:
            merged.append([start,end])
    return merged


def pages_in(spans):
    return sum(end-start for start,end in spans)


def split_limit(intervals, first, last, allowance):
    allowed, out_of_range, exhausted = [], [], []
    for start,end in intervals:
        low,high = max(first,start),min(last+1,end)
        if low >= high:
            out_of_range.append([start,end])
            continue
        if start < low:
            out_of_range.append([start,low])
        if high < end:
            out_of_range.append([high,end])
        taken = min(allowance, high-low)
        if taken:
            allowed.append([low,low+taken])
        if low+taken < high:
            exhausted.append([low+taken,high])
        allowance -= taken
    return allowed, out_of_range, exhausted


def overlap(a, b):
    """Intersection of two half-open intervals, or None when disjoint."""
    low, high = max(a[0], b[0]), min(a[1], b[1])
    return [low, high] if low < high else None


def intersect(intervals, cover):
    """Subtract/clip: parts of intervals inside cover and outside cover (both kept as merged runs)."""
    inside, outside = [], []
    for start, end in intervals:
        low, high = max(start, cover[0]), min(end, cover[1])
        if low < high:
            inside.append([low, high])
        if start < low:
            outside.append([start, low])
        if high < end:
            outside.append([high, end])
    return inside, outside


def normalize_cover(spans):
    """条款页段覆盖：闭区间 [起,止] 列表转半开区间并合并。"""
    return merge([[start, end + 1] for start, end in spans])


def merge(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return merged
