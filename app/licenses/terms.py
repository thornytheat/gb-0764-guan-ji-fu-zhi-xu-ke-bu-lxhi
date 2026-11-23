from app.pagination.spans import normalize_cover

SPECIFICITY = {'institution':3,'consortium':2,'public':1}


def eligible(license, row):
    if license['issn'] != row['journal']['issn'] or not license['from'] <= row['received'] < license['until']:
        return False
    scope = license['scope']
    return scope == 'public' or (scope == 'consortium' and license['subject'] in row['org']['groups']) or (
        scope == 'institution' and license['subject'] == row['org']['code'])


def rank(license):
    return SPECIFICITY[license['scope']], license['revision']


def covers(license, page):
    """A term without coverage reaches every page; otherwise the page must fall in its covered spans."""
    spans = license.get('covers')
    if not spans:
        return True
    return any(start <= page < end for start, end in normalize_cover(spans))


def forbids(license):
    """Forbiddance is an explicit mark on an exclusive term; public/consortium terms never forbid."""
    return bool(license.get('forbid')) and license['scope'] == 'institution'
