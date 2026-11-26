"""Search analytics event delivery."""


class EventDeliveryError(OSError):
    """A search event could not be confirmed by the configured destination."""
