from app.licenses.segments import license_spans, resolve_segments
from app.problem import MESSAGES
from .spans import normalize, pages_in, split_limit


def cancellation(row, active, released, accounts):
    target = active.get(row['target'])
    code = row['ruling']
    if not code and target is None:
        code = 'REPEATED_CANCEL' if row['target'] in released else 'UNKNOWN_TARGET'
    if not code and row['org']['code'] != target['org']:
        code = 'WRONG_ORG'
    if not code and row['received'][:4] != target['received'][:4]:
        code = 'BOUNDARY'
    restored, refunds = 0, []
    if not code:
        reference = active.pop(row['target'])
        released.add(row['target'])
        # 各池只退回原签时从该池实扣的页。
        for pool, pages in reference['pool_pages']:
            accounts[pool]['used'] -= pages
            refunds.append({'pool': pool, 'pages': pages})
        restored = reference['licensed']
    return dict(entry=row['entry'], line=row['line'], target=row['target'], restored=restored,
                refunds=refunds, reason=code, target_line=target['line'] if target else None)


def _debit_pool(spans, article, account):
    """同一额度池内的片段合并后按最低可用页扣减，返回许可段与不足段。"""
    granted, shortage = [], []
    for start, end in spans:
        allowed, _outside, exhausted = split_limit([[start, end]], article[0], article[1],
                                                    account['limit'] - account['used'])
        account['used'] += pages_in(allowed)
        account['gross'] += pages_in(allowed)
        granted.extend(allowed)
        shortage.extend(exhausted)
    return granted, shortage


def _within(parts, span):
    return [part for part in parts if span[0] <= part[0] and part[1] <= span[1]]


def allocate(verdicts, pools, terms=None):
    terms = terms or []
    accounts, active, released = {}, {}, set()
    orders, reversals = [], []
    for row in verdicts:
        if row['action'] == 'cancel':
            reversals.append(cancellation(row, active, released, accounts))
            continue
        intervals = normalize(row['pages'])
        total = pages_in(intervals)
        reasons = {}
        chosen = row['license']
        # 无任何有效授权或元数据缺失的订单：维持整单裁决口径。
        if not chosen:
            denied = pending = 0
            if row['ruling'] == 'NO_LICENSE':
                denied, reasons[row['ruling']] = total, total
            else:
                pending, reasons[row['ruling']] = total, total
            result = {'entry': row['entry'], 'line': row['line'], 'received': row['received'],
                      'org': row['org']['code'] if row['org'] else None, 'license': None, 'pool': None,
                      'pools': [], 'requested': total, 'licensed': 0, 'denied': denied, 'pending': pending,
                      'normalized': intervals, 'granted': [], 'outside': [], 'shortage': [],
                      'segments': [], 'pool_pages': [], 'reasons': reasons,
                      'notes': {code: MESSAGES[code] for code in reasons}}
            orders.append(result)
            continue

        article = row['journal']['pages']
        # 先裁出刊物页界内的申请页，越界页单独记拒。
        in_range, outside = [], []
        for start, end in intervals:
            low, high = max(article[0], start), min(article[1] + 1, end)
            if start < low:
                outside.append([start, low])
            if high < end:
                outside.append([high, end])
            if low < high:
                in_range.append([low, high])
        if outside:
            reasons['OUTSIDE_ARTICLE'] = pages_in(outside)

        segments = resolve_segments(dict(row, normalized=in_range), terms)
        # 每条采纳条款（对应一个额度池）只扣一次，合并其全部片段后扣最低可用页。
        adopted = license_spans(segments)
        debits = {}
        for term_id, spans in adopted.items():
            term = next(segment['license'] for segment in segments if segment['license']
                        and segment['license']['id'] == term_id)
            pool_key = f"{row['received'][:4]}/{term['pool']}"
            account = accounts.setdefault(pool_key, {'limit': pools[term['pool']], 'used': 0, 'gross': 0})
            debits[term_id] = (pool_key, *_debit_pool(spans, article, account))

        granted, shortage, pool_lookup, segment_basis = [], [], {}, []
        for segment in segments:
            span = segment['span']
            basis = {'span': span, 'pages': span[1] - span[0],
                     'license': segment['license']['id'] if segment['license'] else None,
                     'candidates': segment['candidates'], 'ruling': segment['ruling'],
                     'pool': None, 'granted': [], 'shortage': []}
            if segment['license'] is not None:
                pool_key, allowed_parts, exhausted_parts = debits[segment['license']['id']]
                basis['pool'] = pool_key
                basis['granted'] = _within(allowed_parts, span)
                basis['shortage'] = _within(exhausted_parts, span)
                got = pages_in(basis['granted'])
                if got:
                    pool_lookup[pool_key] = pool_lookup.get(pool_key, 0) + got
                if basis['shortage']:
                    reasons['QUOTA'] = reasons.get('QUOTA', 0) + pages_in(basis['shortage'])
                granted.extend(basis['granted'])
                shortage.extend(basis['shortage'])
            else:
                reasons[segment['ruling']] = reasons.get(segment['ruling'], 0) + basis['pages']
            segment_basis.append(basis)

        pool_pages = sorted(pool_lookup.items())
        used_pools = [pool for pool, pages in pool_pages if pages]
        licensed = pages_in(granted)
        denied = pages_in(outside) + sum(
            b['pages'] for b in segment_basis if b['ruling'] in ('NO_LICENSE', 'FORBIDDEN')) + pages_in(shortage)
        pending = sum(b['pages'] for b in segment_basis if b['ruling'] == 'VERSION_TIE')
        result = {'entry': row['entry'], 'line': row['line'], 'received': row['received'],
                  'org': row['org']['code'] if row['org'] else None,
                  'license': chosen['id'], 'pool': used_pools[0] if len(used_pools) == 1 else None,
                  'pools': sorted(used_pools),
                  'requested': total, 'licensed': licensed, 'denied': denied, 'pending': pending,
                  'normalized': intervals, 'granted': granted, 'outside': outside, 'shortage': shortage,
                  'segments': segment_basis, 'pool_pages': pool_pages,
                  'reasons': reasons, 'notes': {code: MESSAGES[code] for code in reasons}}
        orders.append(result)
        if licensed:
            active[row['entry']] = result
    return {'copies': orders, 'reversals': reversals,
            'accounts': {key: dict(value, available=value['limit'] - value['used']) for key, value in accounts.items()},
            'cancelled': sorted(released)}
