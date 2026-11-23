from collections import Counter

from app.pagination.spans import pages_in


def reconcile(intake, ledger):
    copies = ledger['copies']
    total = {key: sum(row[key] for row in copies) for key in ('requested', 'licensed', 'denied', 'pending')}
    restored = sum(row['restored'] for row in ledger['reversals'])
    accounts = ledger['accounts']
    source_ok = intake['received'] == intake['accepted'] + len(intake['duplicates']) + intake['isolated']
    orders_ok = all(r['requested'] == r['licensed'] + r['denied'] + r['pending'] for r in copies)
    # 逐段守恒：每段页数必须落到许可、拒绝（禁印/无授权/越界/额度）或待证之一。
    segments_ok = all(
        sum(segment['pages'] for segment in row['segments']) + pages_in(row['outside'])
        == row['licensed'] + row['denied'] + row['pending']
        for row in copies)
    reasons = Counter()
    footprints = []
    for row in copies:
        reasons.update(row['reasons'])
        if row['reasons']:
            footprints.append({'entry': row['entry'], 'line': row['line'], 'reasons': row['reasons']})
    effective = {pool: sum(pages for row in copies if row['entry'] not in ledger['cancelled']
                           for pool_name, pages in row.get('pool_pages', []) if pool_name == pool)
                 for pool in accounts}
    quota_ok = all(a['limit'] == a['used'] + a['available'] and a['used'] == effective[key]
                   for key, a in accounts.items()) and sum(effective.values()) == total['licensed'] - restored
    return dict(total, restored=restored, effective=total['licensed'] - restored,
                balanced=source_ok and orders_ok and quota_ok and segments_ok,
                source_ok=source_ok, quota_ok=quota_ok, segments_ok=segments_ok,
                accounts=accounts, reasons=dict(reasons), explanations=footprints,
                cancellation_errors=[row for row in ledger['reversals'] if row['reason']])
