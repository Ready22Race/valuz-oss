"""Marketplace module errors."""

from __future__ import annotations

from valuz_agent.infra.errors import NotFoundError, ValuzError


class MarketplaceItemNotFound(NotFoundError):
    error_code = 404_731  # HTTP(3) + module(73) + sequence(01)
    message = "Marketplace item not found"


class MarketplaceUpstreamError(ValuzError):
    status_code = 502
    error_code = 502_731
    message = "Marketplace upstream unavailable"
