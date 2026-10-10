"""Read login/profile/reaction capabilities without issuing keys or forum writes."""
import asyncio
import json
import secrets

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from suenmeow.adapters import ClientSettingsParser, Discourse
from suenmeow.database import Database, KV
from suenmeow.security import Vault
from suenmeow.settings import Settings


async def check():
    settings = Settings.env()
    db, vault = Database(settings.database_url), Vault(settings.key_file)
    with db.transaction() as s:
        assert s.get(KV, "control").data["mode"] in ("paused", "read_only")
        connection = vault.open(s.get(KV, "connection:forum").data["cipher"])
    forum = Discourse(connection)
    result = {}
    try:
        await forum.login()
        current = (await forum.read("/session/current.json")).get("current_user", {})
        result["profile"] = {"stable_id": isinstance(current.get("id"), int),
                             "username": bool(current.get("username")),
                             "avatar_template": bool(current.get("avatar_template")),
                             "name_field": "name" in current, "bot_is_admin": current.get("admin") is True}
        response = await forum.client.head("/user-api-key/new")
        result["user_api"] = {"head_status": response.status_code,
                              "version": response.headers.get("auth-api-version")}
        public_key = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        parameters = {"application_name": "SuenMeow login capability preview", "client_id": secrets.token_hex(16),
                      "nonce": secrets.token_hex(16), "scopes": "session_info", "public_key": public_key}
        for name, params in [("manual_preview", parameters), ("redirect_preview", {
            **parameters, "auth_redirect": settings.origin + "/api/auth/forum/callback"})]:
            response = await forum.client.get("/user-api-key/new.json", params=params)
            try:
                data = response.json()
            except ValueError:
                data = {}
            result["user_api"][name] = {"status": response.status_code,
                                        "authorization_fields": [key for key in (
                                            "application_name", "scopes", "localized_scopes", "no_trust_level", "state") if key in data],
                                        "no_trust_level": data.get("no_trust_level"),
                                        "state": data.get("state")}
        response = await forum.client.get("/latest", headers={"Accept": "text/html", "X-Requested-With": "",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"})
        response.raise_for_status()
        assert len(response.content) <= 2097152
        parser = ClientSettingsParser()
        parser.feed(response.text)
        result["reaction_settings"] = {key: parser.settings.get(key) for key in (
            "discourse_reactions_enabled", "discourse_reactions_enabled_reactions", "discourse_reactions_reaction_for_like")}
        profile = (await forum.read("/u/" + connection["username"] + ".json")).get("user", {})
        result["profile"]["public_profile_name_field"] = "name" in profile
        response = await forum.client.get("/discourse-reactions/custom-reactions.json")
        result["reaction_read_status"] = response.status_code
        topic = await forum.read("/t/11957.json")
        posts = topic.get("post_stream", {}).get("posts", [])
        result["post_fields"] = {key: any(key in post for post in posts) for key in (
            "user_id", "current_user_reaction", "reactions", "can_react", "actions_summary")}
        result["forum_writes"] = 0
        result["keys_issued"] = 0
    finally:
        await forum.close()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(check())
