"""Create host-only secrets without displaying them. Run on the deployment MacBook."""
import argparse
import base64
import os
from pathlib import Path
import secrets

def write_private(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as out:
        out.write(value + "\n")

parser = argparse.ArgumentParser()
parser.add_argument("--origin", default="https://suenmeow.p9pijiu.com")
args = parser.parse_args()
root = Path("secrets")
root.mkdir(mode=0o700, exist_ok=True)
for name, value in {
    "encryption.key": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    "database.password": secrets.token_urlsafe(32),
    "admin.password": secrets.token_urlsafe(24),
}.items():
    if not (root / name).exists():
        write_private(root / name, value)
    # Compose mounts individual secret files; the 0700 parent keeps host access private.
    # Read permission inside containers lets their non-root service accounts use these mounts.
    os.chmod(root / name, 0o644)
os.chmod(root, 0o700)
password = (root / "database.password").read_text().strip()
if not Path(".env").exists():
    write_private(Path(".env"), f"DATABASE_URL=postgresql+psycopg://suenmeow:{password}@database/suenmeow\nPUBLIC_ORIGIN={args.origin}")
print("Deployment secrets prepared. No passwords displayed. Administrator: admin; password file: secrets/admin.password")
