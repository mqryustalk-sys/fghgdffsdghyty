import hashlib
import secrets
import time
from pathlib import Path
from app.db import make_engine, transaction, Record
from app.engine import initialize

e = make_engine()
initialize(e)
password = secrets.token_urlsafe(24)
salt = secrets.token_bytes(16)
with transaction(e) as s:
    old = s.get(Record, "user:browser-test")
    if old:
        s.delete(old)
        s.flush()
    s.add(
        Record(
            id="user:browser-test",
            kind="user",
            at=time.time(),
            data=dict(salt=salt.hex(), hash=hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1).hex()),
        )
    )
Path("/tmp/forex-browser-password").write_text(password)
Path("/tmp/forex-browser-password").chmod(0o600)
