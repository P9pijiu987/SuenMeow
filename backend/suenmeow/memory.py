"""Local lexical retrieval: no embedding/model request and no prompt authority."""
import re

from .adapters import timestamp


def terms(text):
    words = set(re.findall(r"[a-z0-9_]{2,}", text.casefold()))
    for run in re.findall(r"[\u3400-\u9fff]{2,}", text):
        words.update(run[i:i + 2] for i in range(len(run) - 1))
    return words - {"这个", "那个", "今天", "现在", "可以", "喜欢", "自己", "什么", "觉得", "the", "and", "you"}


def rank_facts(facts, query, topic_id):
    """Facts must already have passed identity/privacy filtering. Retain at most two fallbacks."""
    keywords = terms(query)
    scored = []
    for row, data in facts:
        overlap = len(keywords & terms(data.get("text", "")))
        related = overlap > 0 or data.get("topic_id") == topic_id
        score = overlap / max(1, len(terms(data.get("text", "")))) + (1 if data.get("topic_id") == topic_id else 0)
        recency = timestamp(data.get("source_created")) or row.updated
        scored.append((related, score, recency, row.id, data))
    scored.sort(key=lambda item: item[:4], reverse=True)
    related = [(item[3], item[4]) for item in scored if item[0]]
    fallback = [(item[3], item[4]) for item in scored if not item[0]][:2]
    return related + fallback
