def Field(default=..., default_factory=None, **kw):
    return default_factory() if default_factory else default


class BaseModel:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        for k in getattr(type(self), "__annotations__", {}):
            if not hasattr(self, k):
                d = getattr(type(self), k, None)
                setattr(self, k, [] if d == [] else d)
