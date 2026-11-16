import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import text
from sqlalchemy.orm import Session

from api.models import Run
from dispatcher.core import claim


def add_runs(engine, n, status="queued"):
    for _ in range(n):  # one transaction each, like real submissions (now() is per-transaction)
        with Session(engine) as s, s.begin():
            s.add(Run(id=uuid.uuid4(), language="python3.12", code="print(1)", status=status))


def test_claim_respects_cap_under_concurrent_dispatchers(db):
    add_runs(db, 20)
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(lambda _: claim(db, 5), range(16)))
    ids = [c.id for batch in results for c in batch]
    assert len(ids) == 5 and len(set(ids)) == 5  # never over the cap, never claimed twice
    with db.connect() as c:
        assert c.execute(text("select count(*) from runs where status='dispatching'")).scalar() == 5


def test_claim_counts_active_runs(db):
    add_runs(db, 2, "running")
    add_runs(db, 10)
    assert len(claim(db, 5)) == 3
    assert claim(db, 5) == []


def test_claim_is_fifo(db):
    add_runs(db, 3)
    first = claim(db, 1)
    with db.connect() as c:
        oldest = c.execute(text("select id from runs order by created_at limit 1")).scalar()
    assert first[0].id == str(oldest)
