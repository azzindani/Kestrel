"""TelegramNotifier master switch (TELEGRAM_ENABLED)."""

from unittest.mock import AsyncMock, MagicMock, patch

from src.notify.telegram import TelegramNotifier
from tests.helpers.factories import make_app_config


async def test_disabled_notifier_opens_no_client_and_sends_nothing():
    notifier = TelegramNotifier(make_app_config(telegram_enabled=False))
    await notifier.start()
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
        await notifier.send("hello", "INFO")
    post.assert_not_called()
    assert notifier._client is None
    await notifier.stop()


async def test_enabled_notifier_posts_message():
    notifier = TelegramNotifier(make_app_config(telegram_enabled=True))
    await notifier.start()
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=MagicMock()) as post:
        await notifier.send("hello", "INFO")
    post.assert_called_once()
    assert post.call_args.kwargs["json"]["text"].endswith("hello")
    await notifier.stop()
