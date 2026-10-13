from .database import Database, KV, now
from .settings import Settings

def main():
    db = Database(Settings.env().database_url)
    with db.transaction() as s:
        state = s.get(KV, "worker").data
        if now() - state.get("heartbeat", 0) > 60 or state.get("status") == "stopped":
            raise SystemExit(1)

if __name__ == "__main__":
    main()
