"""Telegram Bot API integration for alert notifications."""
import logging
import requests
from typing import Dict

logger = logging.getLogger(__name__)

AGENT_LABELS = {
    "covered_call": "Covered Call",
    "cash_secured_put": "Cash-Secured Put",
    "open_call_monitor": "Open Call Monitor",
    "open_put_monitor": "Open Put Monitor",
}


class TelegramNotifier:
    """Sends alert notifications via Telegram Bot API."""

    def __init__(self, bot_token: str, chat_id: str, enabled: bool = True):
        """Initialize the notifier.

        Args:
            bot_token: Telegram Bot API token from @BotFather
            chat_id: Target chat/group/channel ID
            enabled: Whether notifications are active
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = enabled
        self.api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    def send_alert(
        self,
        symbol: str,
        agent_type: str,
        alert_data: Dict,
        is_roll: bool = False,
    ) -> bool:
        """Send a formatted alert notification.

        Args:
            symbol: Stock symbol (e.g. "AAPL")
            agent_type: Agent type (e.g. "covered_call", "open_call_monitor")
            alert_data: The alert data dict with keys like activity, strike,
                        expiration, confidence, etc.
            is_roll: Whether this is a roll/close alert from a position monitor

        Returns:
            True if sent successfully, False otherwise
        """
        if not self.enabled:
            return False

        try:
            label = AGENT_LABELS.get(agent_type, agent_type)
            if is_roll:
                text = self._format_roll_alert(symbol, label, alert_data)
            else:
                text = self._format_sell_alert(symbol, label, alert_data)
            return self.send_message(text)
        except Exception:
            logger.warning("Failed to build Telegram alert for %s", symbol, exc_info=True)
            return False

    # ── message formatting ────────────────────────────────────────────

    @staticmethod
    def _format_sell_alert(symbol: str, agent_label: str, data: Dict) -> str:
        strike = data.get("strike", "N/A")
        expiration = data.get("expiration", "N/A")
        confidence = data.get("confidence", "N/A")

        lines = [
            f"\U0001f6a8 <b>SELL Alert: {symbol}</b>",
            f"Agent: {agent_label}",
            f"Strike: ${strike}",
            f"Expiration: {expiration}",
            f"Confidence: {confidence}",
        ]
        return "\n".join(lines)

    @staticmethod
    def _format_roll_alert(symbol: str, agent_label: str, data: Dict) -> str:
        action = data.get("action", "N/A")
        current_strike = data.get("current_strike", data.get("strike", "N/A"))
        current_exp = data.get("current_expiration", data.get("expiration", "N/A"))
        new_strike = data.get("new_strike", "N/A")
        new_exp = data.get("new_expiration", "N/A")
        confidence = data.get("confidence", "N/A")

        lines = [
            f"\U0001f504 <b>ROLL Alert: {symbol}</b>",
            f"Agent: {agent_label}",
            f"Action: {action}",
            f"Current: ${current_strike} exp {current_exp}",
            f"New: ${new_strike} exp {new_exp}",
            f"Confidence: {confidence}",
        ]
        return "\n".join(lines)

    # ── low-level send ────────────────────────────────────────────────

    def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a raw message. Low-level.

        Returns:
            True if sent successfully, False otherwise
        """
        if not self.enabled:
            return False

        try:
            resp = requests.post(
                self.api_url,
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                },
                timeout=10,
            )
            if resp.ok:
                logger.info("Telegram message sent (chat_id=%s)", self.chat_id)
                return True
            logger.warning(
                "Telegram API error %s: %s", resp.status_code, resp.text,
            )
            return False
        except Exception:
            logger.warning("Telegram send failed", exc_info=True)
            return False
