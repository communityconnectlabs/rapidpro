class MockRedis:
    source: dict = {}

    def __init__(self, source: dict = None) -> None:
        if source is not None:
            self.source = source
        self.connection_pool = MockConnectionPool()

    def get(self, *args, **kwargs):
        return self.source.get(*args, **kwargs)

    def set(self, *args, **kwargs):
        return self.source.set(*args, **kwargs)

    def expire(self, *args, **kwargs):
        print("-->Test")
        return None


class MockConnectionPool:
    def reset(self):
        """
        Mock the reset functionality of the connection pool.
        """
        print("Mock connection pool reset called.")
