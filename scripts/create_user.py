import getpass
import hashlib
import secrets
import sys
import time
from app.db import make_engine, transaction, Record
from app.engine import initialize

engine = make_engine()
initialize(engine)
username = input("Username: ")
password = getpass.getpass("Password (12+ characters): ")
if len(password) < 12:
    sys.exit("Password too short")
salt = secrets.token_bytes(16)
with transaction(engine) as s:
    if s.get(Record, "user:" + username):
        sys.exit("User exists")
    s.add(
        Record(
            id="user:" + username,
            kind="user",
            at=time.time(),
            data=dict(salt=salt.hex(), hash=hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1).hex(), role="admin"),
        )
    )
print("Created administrator; paper engine initialized and paused.")
