from hashlib import sha256
import json
import sqlite3

from app.problem import DeskError
from app.vault.record import encode, volume


def _postings(stages):
    """按实际许可页汇总每个年度池的毛扣页，以及撤销从该池退回的实退页。"""
    debits, refunds = {}, {}
    for copy in stages['pages']['copies']:
        for pool, pages in copy.get('pool_pages', []):
            debits[pool] = debits.get(pool, 0) + pages
    for reversal in stages['pages']['reversals']:
        if reversal['reason']:
            continue
        for item in reversal.get('refunds', []):
            refunds[item['pool']] = refunds.get(item['pool'], 0) + item['pages']
    return [{'pool': pool, 'debit': debits.get(pool, 0), 'refund': refunds.get(pool, 0),
             'net': debits.get(pool, 0) - refunds.get(pool, 0)}
            for pool in sorted(set(debits) | set(refunds))]


def stamp(vault, run):
    report = volume(vault, run)
    stages = report['stages']
    document = {'document': '馆际复制签发凭证', 'manifest': report['manifest'],
                'accounting': stages['balance'], 'requests': stages['pages']['copies'],
                'revocations': stages['pages']['reversals'], 'isolated': stages['intake']['exceptions'],
                'suppressions': stages['intake']['duplicates'], 'resolutions': stages['licenses']['verdicts'],
                'postings': _postings(stages)}
    body = encode(document)
    imprint = sha256(body.encode()).hexdigest()
    postings = document['postings']
    with vault.session() as conn:
        # 同一事务内写印鉴与逐池台账；任何一步冲突整体回滚，并发落印只有一份生效。
        try:
            new = conn.execute('INSERT INTO slips VALUES (?,?,?)', (run, imprint, body)).rowcount
        except sqlite3.IntegrityError:
            new = 0
        if new:
            conn.executemany('INSERT INTO postings(run_id,pool,debit,refund) VALUES (?,?,?,?)',
                             [(run, item['pool'], item['debit'], item['refund']) for item in postings])
        signed = conn.execute('SELECT digest FROM slips WHERE id=?', (run,)).fetchone()[0]
    return {'run_id': run, 'imprint': signed, 'reused': not bool(new),
            'postings': postings if new else ledger(vault, run)}


def ledger(vault, run=None):
    """读取已落印台账，可按卷号过滤；用于签发凭证与实际许可页对账。"""
    with vault.session() as conn:
        if run is None:
            rows = conn.execute('SELECT run_id,pool,debit,refund,debit-refund FROM postings').fetchall()
            return [{'run_id': r[0], 'pool': r[1], 'debit': r[2], 'refund': r[3], 'net': r[4]} for r in rows]
        rows = conn.execute('SELECT pool,debit,refund,debit-refund FROM postings '
                            'WHERE run_id=?', (run,)).fetchall()
    return [{'pool': r[0], 'debit': r[1], 'refund': r[2], 'net': r[3]} for r in rows]


def certificate(vault, run):
    volume(vault, run)
    with vault.session() as conn:
        record = conn.execute('SELECT digest,body FROM slips WHERE id=?', (run,)).fetchone()
    if record is None:
        raise DeskError('DRAFT_ONLY', 409)
    fingerprint, body = record
    return {'imprint': fingerprint, 'verified': sha256(body.encode()).hexdigest() == fingerprint,
            'document': json.loads(body)}
