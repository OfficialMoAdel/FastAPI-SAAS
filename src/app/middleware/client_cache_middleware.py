from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint


class ClientCacheMiddleware(BaseHTTPMiddleware):
    """Middleware to set the `Cache-Control` header for client-side caching.

    Security-aware caching:
    - Static/public paths: Uses public caching with max-age
    - API/auth paths: Uses private, no-store to prevent sensitive data leakage

    Parameters
    ----------
    app: FastAPI
        The FastAPI application instance.
    max_age: int, optional
        Duration (in seconds) for which public responses should be cached. Defaults to 60 seconds.
    public_paths: tuple, optional
        Path prefixes that are safe to cache publicly. Defaults to ("/static/", "/public/").

    Note
    ----
        - Only explicitly listed public paths are cached publicly
        - All other paths default to private, no-store for security
    """

    def __init__(
        self,
        app: FastAPI,
        max_age: int = 60,
        public_paths: tuple[str, ...] = ("/static/", "/public/", "/favicon.ico"),
    ) -> None:
        super().__init__(app)
        self.max_age = max_age
        self.public_paths = public_paths

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Process the request and set appropriate Cache-Control header.

        Parameters
        ----------
        request: Request
            The incoming request.
        call_next: RequestResponseEndpoint
            The next middleware or route handler in the processing chain.

        Returns
        -------
        Response
            The response object with the appropriate `Cache-Control` header set.
        """
        response: Response = await call_next(request)

        # Only cache static/public resources publicly
        if request.url.path.startswith(self.public_paths):
            response.headers["Cache-Control"] = f"public, max-age={self.max_age}"
        else:
            # Secure default: prevent caching of API responses
            response.headers["Cache-Control"] = "private, no-store"

        return response
