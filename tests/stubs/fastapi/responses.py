class _Resp:
    def __init__(self, content=None, status_code=200):
        self.content, self.status_code, self.headers, self.cookies, self.deleted = content, status_code, {}, {}, []

    def set_cookie(self, key, value, **kw):
        self.cookies[key] = {"value": value, **kw}

    def delete_cookie(self, key, **kw):
        self.deleted.append((key, kw))


class JSONResponse(_Resp):
    pass


class HTMLResponse(_Resp):
    pass


class FileResponse(_Resp):
    def __init__(self, path, **kw):
        super().__init__(None)
        self.path = path
