"""Lectura anónima de metadatos de Spotify (sin credenciales). Ver NOTICE.md."""
from .client import ScraperClient, parse_ref
from .errors import (NetworkError, NotFoundError, ParsingError, RateLimitedError, ScraperError,
                     TokenError, URLError)

__all__ = ["ScraperClient", "parse_ref", "ScraperError", "NetworkError", "NotFoundError",
           "ParsingError", "RateLimitedError", "TokenError", "URLError"]
