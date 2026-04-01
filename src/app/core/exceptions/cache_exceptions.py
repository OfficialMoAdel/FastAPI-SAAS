class CacheException(Exception):
    """Base exception for all cache-related errors."""

    pass


class CacheIdentificationInferenceError(CacheException):
    def __init__(self, message: str = "Could not infer id for resource being cached.") -> None:
        self.message = message
        super().__init__(self.message)


class InvalidRequestError(CacheException):
    def __init__(self, message: str = "Type of request not supported.") -> None:
        self.message = message
        super().__init__(self.message)


class MissingClientError(CacheException):
    def __init__(self, message: str = "Client is None.") -> None:
        self.message = message
        super().__init__(self.message)
