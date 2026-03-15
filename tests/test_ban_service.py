from bot.ban_service import BanService


def test_ban_command_detected() -> None:
    service = BanService("suen")
    assert service.contains_ban_command("/ban @suen") is True
    assert service.contains_ban_command("hello") is False
