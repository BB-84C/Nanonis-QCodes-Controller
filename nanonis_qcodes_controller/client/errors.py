class NanonisClientError(RuntimeError):
    pass


class NanonisConnectionError(NanonisClientError):
    pass


class NanonisTimeoutError(NanonisClientError):
    pass


class NanonisProtocolError(NanonisClientError):
    pass


class NanonisInvalidArgumentError(NanonisClientError):
    pass


class NanonisCommandUnavailableError(NanonisClientError):
    pass
