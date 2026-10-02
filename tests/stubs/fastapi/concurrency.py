async def run_in_threadpool(fn, *a, **kw):
    return fn(*a, **kw)
