"""Shared metadata scope checks. Visibility and source evidence still need a live check."""
import re


def wants_all_memories(text):
    return bool(re.search(r"全部|所有|完整|一切|\ball\b", text, re.IGNORECASE) and
                re.search(r"记忆|記憶|记得|記得|\bmemor(?:y|ies)\b", text, re.IGNORECASE))


def in_scope(fact, site, private, topic_id):
    scope = fact.get("scope", "public")
    if scope not in ("public", "private"):
        return False
    if fact.get("site") and fact["site"] != site:
        return False
    if scope == "private" and (fact.get("site") != site or not private or fact.get("topic_id") != topic_id):
        return False
    if fact.get("origin") == "personal_topic" and fact.get("site") != site:
        return False
    return type(fact.get("topic_id")) is int and fact["topic_id"] > 0


def matches(fact, username="", user_id=0, topic_id=0, keyword=""):
    if user_id and fact.get("forum_user_id") and fact["forum_user_id"] != user_id:
        return False
    # A stable ID allows a renamed user to retain their own sourced facts.
    identity = user_id and fact.get("forum_user_id") == user_id
    if username and not identity and fact.get("username", "").casefold() != username.casefold():
        return False
    if topic_id and fact.get("topic_id") != topic_id:
        return False
    return not keyword or keyword.casefold() in fact.get("text", "").casefold()


def source_matches(fact, post, source_text):
    if post.get("id") != fact.get("source_post_id") or post.get("identity_message"):
        return False
    if fact.get("forum_user_id"):
        if post.get("user_id") != fact["forum_user_id"]:
            return False
    elif post.get("username", "").casefold() != fact.get("username", "").casefold():
        return False
    if fact.get("origin") == "personal_topic":
        quote = fact.get("quote", "")
        if not isinstance(quote, str) or not quote.strip() or quote not in source_text(post):
            return False
    return True
