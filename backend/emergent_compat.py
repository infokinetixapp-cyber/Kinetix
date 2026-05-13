"""Drop-in replacement for `emergentintegrations` so the same code works on
hosting providers (Render, Railway, Fly.io, ...) that don't have access to
Emergent's private package index.

It exposes the exact same classes/methods the original library does but uses
the official SDKs underneath (`google-genai` for LLM, `stripe` for Checkout).
This lets us deploy without depending on a private wheel.
"""
from __future__ import annotations

import os
import uuid
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import stripe as _stripe_sdk

logger = logging.getLogger(__name__)


# =============================================================================
#  LLM: minimal LlmChat/UserMessage clone backed by google-genai (Gemini)
# =============================================================================

@dataclass
class UserMessage:
    text: str


class LlmChat:
    """Mimics emergentintegrations.llm.chat.LlmChat just enough for our use.

    Usage:
        chat = LlmChat(api_key=..., session_id=..., system_message=...)
        chat = chat.with_model("gemini", "gemini-2.5-flash")
        reply: str = await chat.send_message(UserMessage(text="hola"))
    """

    def __init__(
        self,
        api_key: str,
        session_id: Optional[str] = None,
        system_message: Optional[str] = None,
    ) -> None:
        self.api_key = api_key
        self.session_id = session_id or str(uuid.uuid4())
        self.system_message = system_message or ""
        self._provider = "gemini"
        self._model = "gemini-2.5-flash"

    def with_model(self, provider: str, model: str) -> "LlmChat":
        self._provider = provider
        self._model = model
        return self

    async def send_message(self, msg: UserMessage) -> str:
        """Returns the assistant's reply as a plain string."""
        if self._provider != "gemini":
            raise NotImplementedError(
                f"LlmChat compat only implements gemini provider; got {self._provider!r}"
            )

        # Lazy import to avoid hard dependency at module load
        try:
            from google import genai
            from google.genai import types as genai_types
        except Exception as e:
            raise RuntimeError(
                "google-genai is not installed. Add `google-genai` to requirements.txt"
            ) from e

        client = genai.Client(api_key=self.api_key)
        config = None
        if self.system_message:
            config = genai_types.GenerateContentConfig(system_instruction=self.system_message)
        # Run blocking SDK call in a worker thread so we keep async semantics
        import asyncio
        def _call():
            return client.models.generate_content(
                model=self._model,
                contents=msg.text,
                config=config,
            )
        try:
            resp = await asyncio.to_thread(_call)
        except Exception as e:
            logger.exception("LlmChat gemini call failed")
            raise

        # google-genai returns .text directly for simple text completions
        text = getattr(resp, "text", None)
        if text is None:
            # Fallback: try to pull from candidates
            try:
                text = resp.candidates[0].content.parts[0].text
            except Exception:
                text = str(resp)
        return text.strip()


# =============================================================================
#  Stripe Checkout: minimal StripeCheckout / CheckoutSessionRequest clone
# =============================================================================

@dataclass
class CheckoutSessionRequest:
    amount: float
    currency: str
    success_url: str
    cancel_url: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class _CheckoutSessionResult:
    session_id: str
    url: str


class StripeCheckout:
    """Minimal wrapper around stripe.checkout.Session.create that matches the
    method signature server.py was using (`await create_checkout_session(req)`).
    """

    def __init__(self, api_key: str, webhook_url: Optional[str] = None) -> None:
        if not api_key:
            raise ValueError("STRIPE_API_KEY missing")
        self.api_key = api_key
        self.webhook_url = webhook_url  # Kept for API parity; not used here

    async def create_checkout_session(self, req: CheckoutSessionRequest) -> _CheckoutSessionResult:
        import asyncio

        # Stripe expects minor units (cents)
        unit_amount = int(round(req.amount * 100))
        currency = (req.currency or "eur").lower()
        # Use product_data for a one-shot product description (avoids creating Stripe Products)
        product_name = (req.metadata or {}).get("routine_name") \
            or (req.metadata or {}).get("package_id") \
            or "Kinetix purchase"

        def _create():
            _stripe_sdk.api_key = self.api_key
            session = _stripe_sdk.checkout.Session.create(
                mode="payment",
                line_items=[{
                    "price_data": {
                        "currency": currency,
                        "unit_amount": unit_amount,
                        "product_data": {"name": str(product_name)},
                    },
                    "quantity": 1,
                }],
                success_url=req.success_url,
                cancel_url=req.cancel_url,
                customer_email=(req.metadata or {}).get("user_email"),
                metadata=req.metadata or {},
            )
            return session

        try:
            session = await asyncio.to_thread(_create)
        except Exception:
            logger.exception("StripeCheckout.create_checkout_session failed")
            raise

        return _CheckoutSessionResult(session_id=session.id, url=session.url)


# =============================================================================
#  Module-level shim so `from emergent_compat.llm.chat import LlmChat`-style
#  imports work too if anyone wants them. Keep simple — we won't need this.
# =============================================================================
