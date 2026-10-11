import re
from urllib.parse import urlsplit


def topic_link(url, site):
    parsed, origin = urlsplit(url), urlsplit(site)
    if parsed.scheme != 'https' or parsed.netloc != origin.netloc or parsed.username or parsed.password:
        return 0, 0
    parts = parsed.path.strip('/').split('/')
    if len(parts) < 2 or parts[0] != 't':
        return 0, 0
    index = 1 if parts[1].isdigit() else 2
    if len(parts) <= index or not parts[index].isdigit():
        return 0, 0
    tid = int(parts[index]); number = int(parts[index+1]) if len(parts) > index+1 and parts[index+1].isdigit() else 0
    return (tid, number) if tid > 0 else (0, 0)


def requested_topics(text, site):
    if not re.search(r'读|讀|看看|阅读|閱讀|总结|總結|点评|點評|分析|介绍|介紹|\bread\b', text, re.I):
        return []
    return list(dict.fromkeys(tid for url in re.findall(r'https?://[^\s<>\[\]()]+', text)
                              if (tid := topic_link(url, site)[0])))[:3]
