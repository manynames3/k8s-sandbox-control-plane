"""Admin CLI: python -m api.keys create <name>  (prints the key once; only its hash is stored)."""
import sys

from sqlalchemy.orm import Session

from api.auth import hash_key, new_key
from api.db import get_engine
from api.models import ApiKey


def create(name: str) -> str:
    key = new_key()
    with Session(get_engine()) as s, s.begin():
        s.add(ApiKey(name=name, key_hash=hash_key(key)))
    return key


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "create":
        sys.exit("usage: python -m api.keys create <name>")
    print(create(sys.argv[2]))
