from bot.forum_client import ForumClient


def test_cooked_to_text_strips_html() -> None:
    cooked = "<p>Hello <strong>world</strong><br>line 2</p>"
    assert ForumClient.cooked_to_text(cooked) == "Hello world line 2"


def test_normalize_post_extracts_raw_text() -> None:
    normalized = ForumClient.normalize_post(
        {
            "id": 1,
            "topic_id": 2,
            "post_number": 3,
            "reply_to_post_number": None,
            "username": "alice",
            "cooked": "<p>Hi there</p>",
        }
    )
    assert normalized["raw_text"] == "Hi there"
    assert normalized["reply_to_post_number"] == 0


def test_classify_notification_distinguishes_direct_trigger() -> None:
    notification_type, is_direct_trigger = ForumClient.classify_notification({"notification_type": 1})
    assert notification_type == "mentioned"
    assert is_direct_trigger is True

    notification_type, is_direct_trigger = ForumClient.classify_notification({"notification_type": 2})
    assert notification_type == "replied"
    assert is_direct_trigger is True

    notification_type, is_direct_trigger = ForumClient.classify_notification({"notification_type": 3})
    assert notification_type == "quoted"
    assert is_direct_trigger is True

    notification_type, is_direct_trigger = ForumClient.classify_notification({"notification_type": 5})
    assert notification_type == "liked"
    assert is_direct_trigger is False

    notification_type, is_direct_trigger = ForumClient.classify_notification({"notification_type": 6})
    assert notification_type == "private_message"
    assert is_direct_trigger is False

    notification_type, is_direct_trigger = ForumClient.classify_notification({"notification_type": 12})
    assert notification_type == "group_mentioned"
    assert is_direct_trigger is False
