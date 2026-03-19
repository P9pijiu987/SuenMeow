from __future__ import annotations

from dataclasses import dataclass


FIRST_POST_MARKER = "[首帖]"
RECENT_REPLY_MARKER = "[前文回帖]"


@dataclass(slots=True)
class ContextSlice:
    topic_id: int
    post_count: int
    content: str


class ContextBuilder:
    def __init__(self, planner_max_posts: int, replyer_max_posts: int) -> None:
        self.planner_max_posts = planner_max_posts
        self.replyer_max_posts = replyer_max_posts

    def build_for_planner(self, topic_id: int, posts: list[dict]) -> ContextSlice:
        selected = self._select_posts(posts, self.planner_max_posts)
        return ContextSlice(topic_id=topic_id, post_count=len(selected), content=self._render_posts(selected))

    def build_for_replyer(self, topic_id: int, posts: list[dict]) -> ContextSlice:
        selected = self._select_posts(posts, self.replyer_max_posts)
        return ContextSlice(topic_id=topic_id, post_count=len(selected), content=self._render_posts(selected))

    def forum_recent_post_limit(self) -> int:
        return min(max(self.planner_max_posts - 1, self.replyer_max_posts - 1, 0), 50)

    @staticmethod
    def _select_posts(posts: list[dict], limit: int) -> list[dict]:
        if not posts:
            return []
        direct_recent_limit = min(max(limit - 1, 0), 50)
        if len(posts) <= direct_recent_limit + 1:
            return posts
        first_post = posts[:1]
        remainder = posts[-direct_recent_limit:]
        selected = first_post + [post for post in remainder if post.get("post_number") != first_post[0].get("post_number")]
        return selected

    def _render_posts(self, posts: list[dict]) -> str:
        rendered = []
        for index, post in enumerate(posts):
            marker = FIRST_POST_MARKER if index == 0 else RECENT_REPLY_MARKER
            reply_to_post_number = post.get("reply_to_post_number") or "topic"
            rendered.append(
                f"{marker} post_number={post.get('post_number')} user={post.get('username')} reply_to={reply_to_post_number}\n{post.get('raw_text', '')}"
            )
        return "\n\n".join(rendered)

    @staticmethod
    def _render(posts: list[dict]) -> str:
        return "\n\n".join(
            f"post_number={post.get('post_number')} user={post.get('username')}\n{post.get('raw_text', '')}" for post in posts
        )
