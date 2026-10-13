class AppError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details=None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


class NotFoundError(AppError):
    def __init__(self, resource: str, resource_id: str):
        super().__init__(404, "not_found", f"{resource} '{resource_id}' was not found")


class ConflictError(AppError):
    def __init__(self, message: str):
        super().__init__(409, "conflict", message)


class ValidationError(AppError):
    def __init__(self, message: str, details=None):
        super().__init__(422, "validation_error", message, details)

