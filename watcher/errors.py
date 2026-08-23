class WatcherError(Exception):
    """Base class for all watcher errors."""


class ConfigError(WatcherError):
    """Raised when config.yaml is malformed or incomplete."""


class FetchError(WatcherError):
    """Raised when a target page could not be retrieved."""


class ExtractionError(WatcherError):
    """Raised when the selector matched nothing on an otherwise valid page."""
