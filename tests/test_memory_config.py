import config
from evaluation import memory


def test_memory_does_not_probe_local_ollama_when_embeddings_disabled(monkeypatch):
    monkeypatch.setattr(config, "EMBED_PROVIDER", "disabled", raising=False)
    monkeypatch.setattr(
        memory.requests,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("local probe")),
    )
    memory._embed_ok = None

    assert memory.embeddings_available() is False


def test_text_fallback_remains_visible_when_vector_store_has_no_table(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "EMBED_PROVIDER", "disabled", raising=False)
    monkeypatch.setattr(config, "STATE_DIR", tmp_path, raising=False)
    monkeypatch.setattr(memory, "_lance_ok", True)
    monkeypatch.setattr(memory, "_db", type("EmptyDb", (), {
        "open_table": lambda self, _name: (_ for _ in ()).throw(RuntimeError("missing table")),
    })())

    assert memory.add_postmortem({"underlying": "SPY", "realized_pl": 2.0}) is True
    assert memory.recent(1)[0]["realized_pl"] == 2.0
    assert memory.count() == 1
