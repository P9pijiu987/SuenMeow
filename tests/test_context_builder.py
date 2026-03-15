from bot.context_builder import ContextBuilder, FIRST_POST_MARKER, RECENT_REPLY_MARKER


def test_context_builder_keeps_first_post_and_last_50_replies_with_reply_targets() -> None:
    posts = [
        {"post_number": 1, "username": "op", "reply_to_post_number": 0, "raw_text": "first post full text"},
    ]
    for index in range(2, 70):
        posts.append(
            {
                "post_number": index,
                "username": f"user{index}",
                "reply_to_post_number": index - 1,
                "raw_text": f"message {index}",
            }
        )

    builder = ContextBuilder(planner_max_posts=120, replyer_max_posts=60)
    content = builder.build_for_planner(1, posts).content

    assert FIRST_POST_MARKER in content
    assert "first post full text" in content
    assert "reply_to=topic" in content
    assert f"{RECENT_REPLY_MARKER} post_number=20" in content
    assert f"{RECENT_REPLY_MARKER} post_number=69" in content
    assert "reply_to=68" in content
    assert f"{RECENT_REPLY_MARKER} post_number=2 user=user2 reply_to=1" not in content
