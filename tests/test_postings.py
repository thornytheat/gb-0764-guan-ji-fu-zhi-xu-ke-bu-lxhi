"""跨池撤销返还、并发落印幂等、凭证逐段依据与台账对账。"""
from concurrent.futures import ThreadPoolExecutor

from app.certificate.slip import ledger, stamp
from app.pagination.spans import pages_in
from app.registry.catalog import load_register
from app.workflow import steps

from tests.test_segments import HEADER, authority


def build_report(auth, rows):
    register = load_register()
    raw = (HEADER + ''.join(rows)).encode()
    return steps(raw, register={'authority': auth, 'issn': register['issn'],
                                'institutions': register['institutions'], 'sha256': 'test'})


def test_cancel_refunds_only_the_actually_debited_pages_per_pool():
    auth = authority({'NORTH-EX': {'subject': '34001', 'covers': [[5, 10]]}})
    rows = [
        'C30,copy,2026-11-02,34001,2049-3630,3-12,\n',
        'R30,cancel,2026-11-03,34001,,,C30\n',
    ]
    report = build_report(auth, rows)
    copy = report['pages']['copies'][0]
    reversal = report['pages']['reversals'][0]
    # 原签：公开池 4 页 + 专属池 6 页。
    assert dict(copy['pool_pages']) == {'2026/NORTH': 6, '2026/OPEN': 4}
    # 撤销时各池只退回本池实扣页，总额 10 页且逐池可对。
    assert reversal['restored'] == 10
    assert {item['pool']: item['pages'] for item in reversal['refunds']} == {
        '2026/NORTH': 6, '2026/OPEN': 4}
    accounts = report['pages']['accounts']
    assert all(account['used'] == 0 for account in accounts.values())
    assert report['balance']['balanced']


def test_failed_cancellation_refunds_nothing_to_any_pool():
    auth = authority({'NORTH-EX': {'subject': '34001', 'covers': [[5, 10]]}})
    rows = [
        'C31,copy,2026-11-02,34001,2049-3630,3-12,\n',
        'R31,cancel,2026-11-03,00123,,,C31\n',   # 机构不符
    ]
    report = build_report(auth, rows)
    reversal = report['pages']['reversals'][0]
    assert reversal['reason'] == 'WRONG_ORG' and reversal['refunds'] == []
    assert sum(a['used'] for a in report['pages']['accounts'].values()) == 10


def test_concurrent_stamps_commit_postings_once(desk):
    client = desk.test_client()
    run = client.post('/api/volumes', json={'sample': 'dirty'}).json['run_id']
    with ThreadPoolExecutor(8) as pool:
        replies = list(pool.map(lambda _: client.post(f'/api/volumes/{run}/stamp'), range(16)))
    assert sum(not item.json['reused'] for item in replies) == 1
    postings = ledger(desk.vault, run)
    # 每池仅一行台账，净额等于复算结果，重试不重复扣页。
    assert len(postings) == len({p['pool'] for p in postings})
    north = next(p for p in postings if p['pool'] == '2026/NORTH')
    assert (north['debit'], north['refund'], north['net']) == (16, 6, 10)
    assert sum(p['net'] for p in postings) == 26


def test_retry_same_volume_returns_identical_postings(desk):
    client = desk.test_client()
    run = client.post('/api/volumes', json={'sample': 'dirty'}).json['run_id']
    first = client.post(f'/api/volumes/{run}/stamp').json
    second = stamp(desk.vault, run)
    assert second['reused'] and second['postings'] == first['postings']


def test_certificate_carries_per_segment_basis_and_pool_postings(desk):
    client = desk.test_client()
    run = client.post('/api/volumes', json={'sample': 'dirty'}).json['run_id']
    client.post(f'/api/volumes/{run}/stamp')
    document = client.get(f'/api/volumes/{run}/export').json['document']
    # 凭证内含逐段依据与逐池台账，可按实际许可页对账。
    assert 'postings' in document and document['postings']
    first = document['requests'][0]
    assert first['segments'] and all('license' in segment and 'pool' in segment
                                     for segment in first['segments'])
    accounted = sum(p['net'] for p in document['postings'])
    assert accounted == document['manifest']['totals']['effective']


def test_ledger_endpoint_lists_postings(desk):
    client = desk.test_client()
    run = client.post('/api/volumes', json={'sample': 'dirty'}).json['run_id']
    client.post(f'/api/volumes/{run}/stamp')
    scoped = client.get(f'/api/volumes/{run}/ledger').json['postings']
    whole = client.get('/api/ledger').json['postings']
    assert scoped and {item['pool'] for item in scoped} <= {item['pool'] for item in whole}
    assert all(item['run_id'] == run for item in whole)


def test_segment_basis_covers_every_granted_page():
    auth = authority({'NORTH-EX': {'subject': '34001', 'covers': [[5, 10]]}})
    report = build_report(auth, ['C40,copy,2026-11-02,34001,2049-3630,3-12,\n'])
    copy = report['pages']['copies'][0]
    # 逐段许可页之和必须等于整单许可页，每页都能在签发页面查到依据。
    segment_pages = sum(pages_in(segment['granted']) for segment in copy['segments'])
    assert segment_pages == copy['licensed'] == 10
