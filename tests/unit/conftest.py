import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text


@pytest.fixture(scope="session")
def engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.fail("TEST_DATABASE_URL is not set; run `make test` (starts a throwaway Postgres)")
    os.environ["DATABASE_URL"] = url
    import api.db
    api.db._engine = None
    eng = create_engine(url, connect_args={"prepare_threshold": None})
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    subprocess.run([sys.executable, "-m", "alembic", "-c", "api/alembic.ini", "upgrade", "head"],
                   check=True, env=os.environ.copy())
    return eng


@pytest.fixture
def db(engine):
    with engine.begin() as c:
        c.execute(text("TRUNCATE runs, run_events, api_keys, cluster_state CASCADE"))
    return engine
