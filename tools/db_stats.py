"""Print counts of a statekeeper database: states by status/trust, merged duplicates, evidence rows, outcomes by kind.   python tools/db_stats.py <db>"""
import sqlite3
import sys

d = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
print("states by status:", d.execute("select status, count(*) from states group by 1").fetchall())
print("states by trust:", d.execute("select trust, count(*) from states group by 1").fetchall())
print("merged duplicates (rejected 'duplicate of'):", d.execute("select count(*) from states where resolution like '%duplicate of #%'").fetchone()[0])
print("evidence rows:", d.execute("select count(*) from evidence").fetchone()[0])
print("things:", d.execute("select count(*) from things").fetchone()[0])
print("outcomes:", d.execute("select kind, count(*) from outcomes group by 1").fetchall())
