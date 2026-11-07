"""Errores del scraper. Derivado de SpotifyScraper (MIT), ver NOTICE.md."""
from __future__ import annotations


class ScraperError(Exception):
    """Base de todos los errores del scraper."""


class URLError(ScraperError):
    """La referencia no es una URL, URI o ID de Spotify válida."""


class NetworkError(ScraperError):
    """Fallo de red o respuesta HTTP inesperada."""


class RateLimitedError(NetworkError):
    """Spotify respondió 429. `retry_after` son segundos, si lo indicó."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class TokenError(ScraperError):
    """No se pudo obtener o el servidor rechazó el token anónimo."""


class NotFoundError(ScraperError):
    """La entidad no existe o no es pública."""


class ParsingError(ScraperError):
    """El formato de Spotify cambió (hash rotado o JSON distinto)."""
