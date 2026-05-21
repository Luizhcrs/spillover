import pytest


@pytest.fixture(autouse=True)
def clear_kuzu_cache_between_tests():
    from spillover.storage.kuzu import clear_kuzu_cache
    clear_kuzu_cache()
    yield
    clear_kuzu_cache()
