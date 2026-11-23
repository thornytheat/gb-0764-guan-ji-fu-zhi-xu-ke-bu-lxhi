"""跨页段条款裁决：专属条款只覆盖部分页、禁印页、公开许可补齐与逐池对账。"""
from copy import deepcopy

import pytest

from app.registry.catalog import load_register
from app.registry.rules import check_authority
from app.workflow import steps


HEADER = 'entry,action,received,institution,issn,pages,target\n'


def authority(overrides=None, pools=None, validate=True):
    """以登记目录为底本，按条款编号注入覆盖/禁印等改写。"""
    register = load_register()
    data = deepcopy(register['authority'])
    data['terms'] = [
        {'id': 'OPEN-1', 'issn': '2049-3630', 'scope': 'public', 'subject': '*', 'revision': 1,
         'from': '2026-01-01', 'until': '2027-01-01', 'pool': 'OPEN'},
        {'id': 'NET-1', 'issn': '2049-3630', 'scope': 'consortium', 'subject': 'NET', 'revision': 1,
         'from': '2026-01-01', 'until': '2027-01-01', 'pool': 'NET'},
        {'id': 'NORTH-EX', 'issn': '2049-3630', 'scope': 'institution', 'subject': '00123', 'revision': 2,
         'from': '2026-01-01', 'until': '2027-01-01', 'pool': 'NORTH'},
    ]
    for term_id, patch in (overrides or {}).items():
        next(term for term in data['terms'] if term['id'] == term_id).update(patch)
    if pools:
        data['pools'] = pools
    if validate:
        check_authority(data)
    return data


def verdict_row(auth, entry='C20', pages='3-20', received='2026-11-02', institution='00123',
                issn='2049-3630'):
    raw = (HEADER + f'{entry},copy,{received},{institution},{issn},{pages},\n').encode()
    register = load_register()
    report = steps(raw, register={'authority': auth, 'issn': register['issn'],
                                  'institutions': register['institutions'], 'sha256': 'test'})
    return report


def test_exclusive_terms_cover_only_named_segments():
    # 专属条款只管 5-10 页，其余页回落到联盟（00123 属 NET，联盟优先于公开）。
    auth = authority({'NORTH-EX': {'covers': [[5, 10]]}})
    report = verdict_row(auth)
    copy = report['pages']['copies'][0]
    basis = {(b['span'][0], b['span'][1]): b for b in copy['segments']}
    assert basis[(3, 5)]['license'] == 'NET-1'
    assert basis[(5, 11)]['license'] == 'NORTH-EX'
    assert basis[(11, 21)]['license'] == 'NET-1'
    assert copy['pools'] == ['2026/NET', '2026/NORTH']
    # NET 池限额 8，12 页回落只放 8 页，其余 4 页记额度不足。
    assert dict(copy['pool_pages']) == {'2026/NET': 8, '2026/NORTH': 6}
    assert copy['reasons']['QUOTA'] == 4


def test_forbidden_pages_are_not_signed_under_public_license():
    # 专属条款只覆盖 5-10 且禁印，任何许可都不得把这 6 页补签出去。
    auth = authority({'NORTH-EX': {'covers': [[5, 10]], 'forbid': True}})
    report = verdict_row(auth, pages='3-12')
    copy = report['pages']['copies'][0]
    forbidden = [b for b in copy['segments'] if b['ruling'] == 'FORBIDDEN']
    assert [b['span'] for b in forbidden] == [[5, 11]]
    assert copy['denied'] == 6 and copy['licensed'] == 4
    assert copy['reasons']['FORBIDDEN'] == 6
    # 禁印段之外的 4 页走联盟池，禁印页绝不进入任何额度池。
    assert dict(copy['pool_pages']) == {'2026/NET': 4}


def test_each_segment_records_its_basis_and_candidates():
    auth = authority({'NORTH-EX': {'covers': [[8, 12]]}})
    report = verdict_row(auth, pages='8-12')
    segment = report['pages']['copies'][0]['segments'][0]
    assert segment['license'] == 'NORTH-EX' and segment['pool'] == '2026/NORTH'
    assert set(segment['candidates']) == {'OPEN-1', 'NET-1', 'NORTH-EX'}


def test_uncovered_pages_fall_back_to_public_for_non_member_org():
    # 34001 不属任何联盟，专属未覆盖页只能由公开许可 OPEN-1 补齐。
    auth = authority({'NORTH-EX': {'subject': '34001', 'covers': [[5, 10]]}})
    report = verdict_row(auth, institution='34001', pages='3-12')
    copy = report['pages']['copies'][0]
    basis = {(b['span'][0], b['span'][1]): b for b in copy['segments']}
    assert basis[(3, 5)]['license'] == 'OPEN-1'
    assert basis[(5, 11)]['license'] == 'NORTH-EX'
    assert basis[(11, 13)]['license'] == 'OPEN-1'
    assert dict(copy['pool_pages']) == {'2026/OPEN': 4, '2026/NORTH': 6}


def test_uncovered_page_with_no_public_license_is_denied():
    # 去掉公开与联盟条款：专属未覆盖的页没有任何当日授权，整段拒绝。
    auth = authority({'NORTH-EX': {'covers': [[5, 10]]}})
    auth['terms'] = [term for term in auth['terms'] if term['id'] == 'NORTH-EX']
    report = verdict_row(auth, pages='3-12')
    copy = report['pages']['copies'][0]
    no_license = [b for b in copy['segments'] if b['ruling'] == 'NO_LICENSE']
    assert sorted(b['span'] for b in no_license) == [[3, 5], [11, 13]]
    assert copy['licensed'] == 6 and copy['denied'] == 4


def test_segment_conservation_holds_in_reconciliation():
    auth = authority({'NORTH-EX': {'covers': [[5, 10]], 'forbid': True}})
    report = verdict_row(auth, pages='3-20')
    assert report['balance']['segments_ok'] and report['balance']['balanced']
    copy = report['pages']['copies'][0]
    assert copy['requested'] == copy['licensed'] + copy['denied'] + copy['pending'] == 18


def test_quota_shortage_is_attributed_to_the_exhausted_pool():
    auth = authority({'NORTH-EX': {'covers': [[3, 20]]}}, pools={'NORTH': 4, 'NET': 8, 'OPEN': 5})
    report = verdict_row(auth, pages='3-20')
    copy = report['pages']['copies'][0]
    # 专属池只够 4 页（最低可用页），其余页并不回退，按额度不足拒绝。
    assert copy['licensed'] == 4 and copy['reasons']['QUOTA'] == 14
    assert dict(copy['pool_pages']) == {'2026/NORTH': 4}


def test_resolver_treats_terms_without_covers_as_full_range():
    report = verdict_row(authority(), pages='3-8')
    segments = report['pages']['copies'][0]['segments']
    assert len(segments) == 1 and segments[0]['license'] == 'NORTH-EX'


def test_invalid_coverage_rejected_by_authority_check():
    auth = authority(validate=False)
    next(term for term in auth['terms'] if term['id'] == 'NORTH-EX')['covers'] = [[99, 120]]
    with pytest.raises(ValueError, match='页段覆盖'):
        check_authority(auth)


def test_forbid_only_allowed_on_institution_terms():
    auth = authority(validate=False)
    next(term for term in auth['terms'] if term['id'] == 'OPEN-1')['forbid'] = True
    with pytest.raises(ValueError, match='禁印'):
        check_authority(auth)
