import pytest

from bot.forum_client import ForumClient
from bot.settings import CredentialsConfig, ForumConfig


class DummyForumClient(ForumClient):
    def __init__(self) -> None:
        super().__init__(
            ForumConfig(base_url="https://forum.example.com", retry=1, user_agent="ua", default_headers={}, reactions={}),
            CredentialsConfig(username="u", password="p"),
            read_only=True,
        )

    async def get_topic(self, topic_id: int) -> dict:
        return {"post_stream": {"stream": list(range(101, 171))}}

    async def get_posts(self, topic_id: int, post_ids: list[int]) -> list[dict]:
        return [
            {"post_number": index - 100, "username": f"u{index}", "raw_text": f"post {index}"}
            for index in post_ids
        ]


@pytest.mark.anyio
async def test_topic_selected_posts_fetches_first_plus_last_50_posts() -> None:
    client = DummyForumClient()
    try:
        posts = await client.get_topic_selected_posts(1, recent_post_limit=50)
    finally:
        await client.aclose()

    assert posts[0]["post_number"] == 1
    assert posts[-1]["post_number"] == 70
    assert posts[1]["post_number"] == 21
    assert len(posts) == 51
