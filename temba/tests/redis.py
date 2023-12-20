class MockRedis:
    source: dict = {}

    def __init__(self, source: dict = None) -> None:
        if source is not None:
            self.source = source

    def get(self, *args, **kwargs):
        return self.source.get(*args, **kwargs)

    def set(self, *args, **kwargs):
        return self.source.set(*args, **kwargs)

    def expire(self, *args, **kwargs):
        print("-->Test")
        return None
