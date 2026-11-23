from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3


class Vault:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.session() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS volumes (id TEXT PRIMARY KEY, manifest TEXT NOT NULL, evidence TEXT NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS slips (id TEXT PRIMARY KEY REFERENCES volumes(id), digest TEXT NOT NULL, body TEXT NOT NULL)')
            # 落印即按池实扣/实退的台账；(卷号,池) 唯一约束保证并发落印或重试不重复扣页。
            conn.execute('CREATE TABLE IF NOT EXISTS postings (run_id TEXT NOT NULL REFERENCES volumes(id), '
                         'pool TEXT NOT NULL, debit INTEGER NOT NULL, refund INTEGER NOT NULL, '
                         'PRIMARY KEY (run_id, pool))')

    @contextmanager
    def session(self):
        with closing(sqlite3.connect(self.path,timeout=10)) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            with conn:
                yield conn
