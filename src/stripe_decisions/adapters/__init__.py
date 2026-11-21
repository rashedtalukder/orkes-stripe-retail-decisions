"""Model adapters for customer-intent classification (interpretation only, never authorization)."""

from .base import IntentClassifier, get_classifier

__all__ = ["IntentClassifier", "get_classifier"]
