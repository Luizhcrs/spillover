import pytest

from spillover.facet.embed import EMBED_DIM, embed_text


@pytest.mark.slow
def test_embed_text_returns_correct_dim():
    v = embed_text("hello world")
    assert len(v) == EMBED_DIM
    assert all(isinstance(x, float) for x in v)


@pytest.mark.slow
def test_embed_text_deterministic():
    v1 = embed_text("the quick brown fox")
    v2 = embed_text("the quick brown fox")
    assert v1 == v2


def test_embed_text_empty():
    v = embed_text("")
    assert v == [0.0] * EMBED_DIM
