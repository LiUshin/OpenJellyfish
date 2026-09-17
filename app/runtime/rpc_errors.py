class RuntimeFailure(RuntimeError):
    pass


class RuntimeUnavailable(RuntimeFailure):
    """The deployment is missing an executable or cannot launch it."""
