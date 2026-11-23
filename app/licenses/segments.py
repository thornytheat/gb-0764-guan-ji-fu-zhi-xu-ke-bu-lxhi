"""按页段裁决适用条款。

一份复本订单可横跨多页，专属条款可能只覆盖其中几段、甚至明令禁印某几段；
其余页才回落到联盟或公开许可。这里把申请页并集切成连续片段，逐片段裁决，
保证：

* 专属（机构）优先于联盟、再到公开；同类高修订号优先；
* 同级同修订号多条款并列时整段待证（VERSION_TIE）；
* 专属条款显式 ``forbid`` 的页判为禁印（FORBIDDEN），不会被公开许可补签；
* 没有任何当日有效条款覆盖的页拒绝（NO_LICENSE）；
* 每个片段都记录依据条款与候选，供签发页面逐段查据。
"""
from app.pagination.spans import merge, normalize_cover
from .terms import covers, eligible, forbids, rank


def _cut_points(interval, valid):
    """条款覆盖边界把区间切成若干裁决恒定的小段。"""
    points = {interval[0], interval[1]}
    for term in valid:
        if not term.get('covers'):
            continue
        for start, end in normalize_cover(term['covers']):
            if interval[0] < start < interval[1]:
                points.add(start)
            if interval[0] < end < interval[1]:
                points.add(end)
    return sorted(points)


def resolve_segments(row, terms):
    """把订单的每个半开页区间切成带裁决的片段列表。

    每项含 ``span``（半开区间）、``license``（采纳条款或 None）、
    ``candidates``（命中条款编号）与 ``ruling``（裁决码或 None）。
    """
    valid = [term for term in terms if eligible(term, row)]
    segments = []
    for interval in row['normalized']:
        cuts = _cut_points(interval, valid)
        for low, high in zip(cuts, cuts[1:]):
            page = low
            covering = [term for term in valid if covers(term, page)]
            chosen, ruling, candidates = None, None, [term['id'] for term in covering]
            if not covering:
                ruling = 'NO_LICENSE'
            else:
                strongest = max(rank(term) for term in covering)
                best = [term for term in covering if rank(term) == strongest]
                if len(best) > 1:
                    ruling = 'VERSION_TIE'
                elif forbids(best[0]):
                    ruling = 'FORBIDDEN'
                else:
                    chosen = best[0]
            segments.append({'span':[low, high], 'license':chosen,
                             'candidates':candidates, 'ruling':ruling})
    return _merge_adjacent(segments)


def _merge_adjacent(segments):
    """合并相邻且裁决一致（同条款、同裁决码、同候选集合）的片段。"""
    merged = []
    for segment in segments:
        if merged:
            previous = merged[-1]
            same_license = (previous['license'] or {}).get('id') == (segment['license'] or {}).get('id')
            if (previous['span'][1] == segment['span'][0] and same_license
                    and previous['ruling'] == segment['ruling']
                    and previous['candidates'] == segment['candidates']):
                previous['span'][1] = segment['span'][1]
                continue
        merged.append(dict(segment))
    return merged


def license_spans(segments):
    """合并各条款实际采纳的片段，便于按池归并扣页。"""
    grouped = {}
    for segment in segments:
        if segment['license'] is None:
            continue
        grouped.setdefault(segment['license']['id'], []).append(segment['span'])
    return {key: merge(spans) for key, spans in grouped.items()}
