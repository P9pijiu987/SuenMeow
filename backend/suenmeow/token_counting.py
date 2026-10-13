"""Offline estimates; provider usage remains authoritative and cache is not a refund."""
from functools import lru_cache
import json
import math
from pathlib import Path
from urllib.parse import urlsplit


def official_v4(conf):
    return (urlsplit(conf.get('base_url', '')).hostname == 'api.deepseek.com'
            and conf.get('model', '') in ('deepseek-flash', 'deepseek-pro', 'deepseek-v4-flash',
                'deepseek-v4.1-flash', 'deepseek-v4-pro', 'deepseek-v4.1-pro', 'deepseek-v4-flash-vision-exp'))


@lru_cache(maxsize=1)
def tokenizer():
    from tokenizers import Tokenizer
    return Tokenizer.from_file(str(Path(__file__).parent / 'assets/deepseek_v4_tokenizer.json'))


def estimate(messages, conf=None, tools=None):
    clean, images = [], 0
    for message in messages:
        value = dict(message)
        if isinstance(value.get('content'), list):
            parts = []
            for part in value['content']:
                if part.get('type') == 'image_url': images += 1
                else: parts.append(part)
            value['content'] = parts
        clean.append(value)
    encoded = json.dumps({'messages': clean, **({'tools': tools} if tools else {})}, ensure_ascii=False, separators=(',', ':'))
    if official_v4(conf or {}):
        # Count structured text, then allow for server chat/tool framing differences.
        text = len(tokenizer().encode(encoded, add_special_tokens=False).ids)
        count = math.ceil(text * 1.10) + 128 + 16 * len(messages) + images * 1024
        method = 'deepseek_v4_local_with_margin'
    else:
        count = len(encoded.encode()) + images * 4096 + 128
        method = 'utf8_upper_bound'
    return count, {'input_estimate': count, 'estimator': method, 'image_count': images}


def usage_metrics(usage):
    """Persist only validated counters, never arbitrary provider fields or text."""
    if not isinstance(usage, dict): return {}
    result = {}
    for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
        if type(usage.get(key)) is int and usage[key] >= 0: result[key] = usage[key]
    cached = usage.get('prompt_cache_hit_tokens')
    details = usage.get('prompt_tokens_details')
    if cached is None and isinstance(details, dict): cached = details.get('cached_tokens')
    prompt = result.get('prompt_tokens')
    if type(cached) is int and cached >= 0 and (prompt is None or cached <= prompt):
        result['cache_hit_tokens'] = cached
    miss = usage.get('prompt_cache_miss_tokens')
    if type(miss) is int and miss >= 0 and (prompt is None or miss <= prompt):
        result['cache_miss_tokens'] = miss
    elif prompt is not None and 'cache_hit_tokens' in result:
        result['cache_miss_tokens'] = prompt - result['cache_hit_tokens']
    if prompt is not None and 'cache_hit_tokens' in result and 'cache_miss_tokens' in result and result['cache_hit_tokens'] + result['cache_miss_tokens'] != prompt:
        result.pop('cache_hit_tokens'); result.pop('cache_miss_tokens')
    completion = usage.get('completion_tokens_details')
    if isinstance(completion, dict) and type(completion.get('reasoning_tokens')) is int and completion['reasoning_tokens'] >= 0:
        result['reasoning_tokens'] = completion['reasoning_tokens']
    return result
