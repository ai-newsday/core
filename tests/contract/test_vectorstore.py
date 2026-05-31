from src.adapters.vectorstore.memory import InMemoryVectorStore


def test_upsert_records_points():
    store = InMemoryVectorStore()
    store.upsert([("id1", [1.0, 0.0], {"cluster_id": "evt-x-001"})])
    assert store.points["id1"] == ([1.0, 0.0], {"cluster_id": "evt-x-001"})


def test_upsert_is_idempotent_by_id():
    store = InMemoryVectorStore()
    store.upsert([("id1", [1.0], {"k": "a"})])
    store.upsert([("id1", [2.0], {"k": "b"})])
    assert store.points["id1"] == ([2.0], {"k": "b"})
    assert len(store.points) == 1


def test_upsert_empty_is_noop():
    store = InMemoryVectorStore()
    store.upsert([])
    assert store.points == {}
