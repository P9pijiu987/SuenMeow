import logging

from bot.logging_utils import configure_logging


def test_configure_logging_creates_file(tmp_path) -> None:
    root_logger = logging.getLogger()
    old_handlers = list(root_logger.handlers)
    try:
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
            handler.close()

        configure_logging(tmp_path)
        logging.getLogger("test").info("hello")
        logging.getLogger("bot.pipeline").info("pipeline message")
        for handler in root_logger.handlers:
            handler.flush()
        assert (tmp_path / "latest.log").exists()
        assert (tmp_path / "reply.log").exists()
    finally:
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
            handler.close()
        for handler in old_handlers:
            root_logger.addHandler(handler)
