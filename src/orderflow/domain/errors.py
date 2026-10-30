class DomainError(Exception):
    def __init__(self, code: str, message: str, status: int = 409, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


def missing_resource() -> DomainError:
    return DomainError("not_found", "Resource does not exist", 404)
