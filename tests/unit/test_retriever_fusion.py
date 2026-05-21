from spillover.retriever.fusion import rrf_fuse
from spillover.retriever.vector import Hit


def test_single_ranking_passthrough():
    r = [Hit("a", 0.9), Hit("b", 0.8)]
    out = rrf_fuse(r)
    assert [h.episode_id for h in out] == ["a", "b"]


def test_fusion_dedup_and_boost():
    r1 = [Hit("a", 0.9, memory_type="episodic"), Hit("b", 0.8, memory_type="episodic")]
    r2 = [Hit("b", 0.95, memory_type="episodic"), Hit("c", 0.5, memory_type="episodic")]
    out = rrf_fuse(r1, r2)
    ids = [h.episode_id for h in out]
    assert ids[0] == "b"
    assert set(ids) == {"a", "b", "c"}


def test_type_weight_boosts_priority():
    r = [
        Hit("ep_low", 0.9, memory_type="episodic"),
        Hit("ep_pri", 0.5, memory_type="priority"),
    ]
    out = rrf_fuse(r)
    assert out[0].episode_id == "ep_pri"


def test_marks_source_fusion():
    out = rrf_fuse([Hit("a", 1.0)])
    assert out[0].source == "fusion"
