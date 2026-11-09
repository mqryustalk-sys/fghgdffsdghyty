"""Bootstrap an isolated hosted demo using an externally configured password hash."""

import os
import time
from app.db import make_engine, transaction, Record
from app.engine import initialize

engine = make_engine()
initialize(engine)
salt = os.environ.get("DEMO_PASSWORD_SALT")
password_hash = os.environ.get("DEMO_PASSWORD_HASH")
if not salt or not password_hash:
    raise SystemExit("Demo login hash and salt must be configured in hosting settings")
with transaction(engine) as session:
    if not session.get(Record, "user:demo"):
        session.add(
            Record(id="user:demo", kind="user", at=time.time(), data={"salt": salt, "hash": password_hash, "role": "admin"})
        )
