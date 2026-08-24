"""Qdrant vector store (ADR-016): client request shapes, search merge, flag."""

import json
from typing import Any, Dict, List

import httpx
import pytest

from open_notebook.vectorstore import qdrant as q
from open_notebook.vectorstore.search import vector_search_qdrant


def make_transport(recorder: List[Dict[str, Any]], responses: Dict[str, Any]):
    """MockTransport recording requests; responses keyed by 'METHOD path'."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = None
        if request.content:
            body = json.loads(request.content)
        recorder.append(
            {"method": request.method, "path": request.url.path, "body": body}
        )
        key = f"{request.method} {request.url.path}"
        spec = responses.get(key, {"status": 200, "json": {"result": {}}})
        return httpx.Response(spec["status"], json=spec["json"])

    return httpx.MockTransport(handler)


@pytest.fixture
def qdrant_env(monkeypatch):
    monkeypatch.setenv("OPEN_NOTEBOOK_VECTOR_STORE", "qdrant")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant.test:6333")
    monkeypatch.setenv("QDRANT_API_KEY", "test-key")
    monkeypatch.delenv("QDRANT_COLLECTION", raising=False)


def patch_transport(monkeypatch, transport):
    monkeypatch.setattr(
        q,
        "_client",
        lambda: httpx.AsyncClient(
            base_url="http://qdrant.test:6333", transport=transport
        ),
    )


class TestBackendFlag:
    def test_default_is_surreal(self, monkeypatch):
        monkeypatch.delenv("OPEN_NOTEBOOK_VECTOR_STORE", raising=False)
        assert q.vector_store_backend() == "surreal"
        assert q.qdrant_enabled() is False

    def test_qdrant_opt_in(self, monkeypatch):
        monkeypatch.setenv("OPEN_NOTEBOOK_VECTOR_STORE", "qdrant")
        assert q.qdrant_enabled() is True

    def test_unknown_value_falls_back(self, monkeypatch):
        monkeypatch.setenv("OPEN_NOTEBOOK_VECTOR_STORE", "banana")
        assert q.vector_store_backend() == "surreal"


class TestPointIds:
    def test_deterministic(self):
        a = q.chunk_point_id("source:abc", 3)
        assert a == q.chunk_point_id("source:abc", 3)
        assert a != q.chunk_point_id("source:abc", 4)
        assert a != q.chunk_point_id("source:xyz", 3)


class TestClientCalls:
    @pytest.mark.asyncio
    async def test_upsert_shape_and_order_offset(self, qdrant_env, monkeypatch):
        recorded: List[Dict[str, Any]] = []
        patch_transport(monkeypatch, make_transport(recorded, {}))

        written = await q.upsert_chunks(
            "source:abc", ["c2", "c3"], [[0.1], [0.2]], start_order=2
        )
        assert written == 2
        call = recorded[0]
        assert call["method"] == "PUT"
        assert call["path"] == "/collections/open_notebook_chunks/points"
        points = call["body"]["points"]
        assert points[0]["payload"] == {
            "source_id": "source:abc",
            "order": 2,
            "content": "c2",
        }
        assert points[0]["id"] == q.chunk_point_id("source:abc", 2)
        assert points[1]["payload"]["order"] == 3

    @pytest.mark.asyncio
    async def test_delete_filters_by_source(self, qdrant_env, monkeypatch):
        recorded: List[Dict[str, Any]] = []
        patch_transport(monkeypatch, make_transport(recorded, {}))

        await q.delete_source_points("source:abc")
        call = recorded[0]
        assert call["path"].endswith("/points/delete")
        assert call["body"]["filter"]["must"][0] == {
            "key": "source_id",
            "match": {"value": "source:abc"},
        }

    @pytest.mark.asyncio
    async def test_search_missing_collection_is_empty(self, qdrant_env, monkeypatch):
        recorded: List[Dict[str, Any]] = []
        patch_transport(
            monkeypatch,
            make_transport(
                recorded,
                {
                    "POST /collections/open_notebook_chunks/points/search": {
                        "status": 404,
                        "json": {"status": {"error": "not found"}},
                    }
                },
            ),
        )
        assert await q.search_points([0.1], 5) == []

    @pytest.mark.asyncio
    async def test_facet_source_counts(self, qdrant_env, monkeypatch):
        recorded: List[Dict[str, Any]] = []
        patch_transport(
            monkeypatch,
            make_transport(
                recorded,
                {
                    "POST /collections/open_notebook_chunks/facet": {
                        "status": 200,
                        "json": {
                            "result": {
                                "hits": [
                                    {"value": "source:a", "count": 12},
                                    {"value": "source:b", "count": 3},
                                ]
                            }
                        },
                    }
                },
            ),
        )
        counts = await q.facet_source_counts()
        assert counts == {"source:a": 12, "source:b": 3}
        assert recorded[0]["body"]["key"] == "source_id"

    @pytest.mark.asyncio
    async def test_facet_missing_collection_is_empty(self, qdrant_env, monkeypatch):
        recorded: List[Dict[str, Any]] = []
        patch_transport(
            monkeypatch,
            make_transport(
                recorded,
                {
                    "POST /collections/open_notebook_chunks/facet": {
                        "status": 404,
                        "json": {"status": {"error": "not found"}},
                    }
                },
            ),
        )
        assert await q.facet_source_counts() == {}

    @pytest.mark.asyncio
    async def test_count_source_points_filters(self, qdrant_env, monkeypatch):
        recorded: List[Dict[str, Any]] = []
        patch_transport(
            monkeypatch,
            make_transport(
                recorded,
                {
                    "POST /collections/open_notebook_chunks/points/count": {
                        "status": 200,
                        "json": {"result": {"count": 7}},
                    }
                },
            ),
        )
        assert await q.count_source_points("source:abc") == 7
        body = recorded[0]["body"]
        assert body["filter"]["must"][0]["match"]["value"] == "source:abc"

    @pytest.mark.asyncio
    async def test_ensure_collection_skips_existing(self, qdrant_env, monkeypatch):
        recorded: List[Dict[str, Any]] = []
        patch_transport(
            monkeypatch,
            make_transport(
                recorded,
                {
                    "GET /collections/open_notebook_chunks": {
                        "status": 200,
                        "json": {"result": {}},
                    }
                },
            ),
        )
        await q.ensure_collection(768)
        # collection GET + payload-index PUT; no collection-create PUT after 200
        assert len(recorded) == 2
        assert recorded[1]["path"].endswith("/index")


class TestSearchMerge:
    @pytest.mark.asyncio
    async def test_chunks_grouped_and_merged_with_surreal(
        self, qdrant_env, monkeypatch
    ):
        hits = [
            {
                "score": 0.9,
                "payload": {"source_id": "source:a", "order": 0, "content": "A0"},
            },
            {
                "score": 0.8,
                "payload": {"source_id": "source:a", "order": 1, "content": "A1"},
            },
            {
                "score": 0.7,
                "payload": {"source_id": "source:b", "order": 0, "content": "B0"},
            },
        ]
        recorded: List[Dict[str, Any]] = []
        patch_transport(
            monkeypatch,
            make_transport(
                recorded,
                {
                    "POST /collections/open_notebook_chunks/points/search": {
                        "status": 200,
                        "json": {"result": hits},
                    }
                },
            ),
        )

        async def fake_repo_query(sql: str, params=None):
            if "fn::vector_search" in sql:
                return [
                    {
                        "id": "note:n1",
                        "parent_id": "note:n1",
                        "title": "A note",
                        "similarity": 0.85,
                        "matches": ["note text"],
                    }
                ]
            if "FROM source" in sql:
                return [
                    {"id": "source:a", "title": "Source A"},
                    {"id": "source:b", "title": "Source B"},
                ]
            raise AssertionError(f"unexpected query: {sql}")

        import open_notebook.vectorstore.search as search_mod

        monkeypatch.setattr(search_mod, "repo_query", fake_repo_query)
        monkeypatch.setattr(search_mod, "ensure_record_id", lambda x: x)

        rows = await vector_search_qdrant([0.1], 10, True, True, 0.2)

        assert [r["id"] for r in rows] == ["source:a", "note:n1", "source:b"]
        source_a = rows[0]
        assert source_a["title"] == "Source A"
        assert source_a["similarity"] == 0.9
        assert source_a["matches"] == ["A0", "A1"]
        assert source_a["parent_id"] == "source:a"

    @pytest.mark.asyncio
    async def test_sources_false_skips_qdrant(self, qdrant_env, monkeypatch):
        recorded: List[Dict[str, Any]] = []
        patch_transport(monkeypatch, make_transport(recorded, {}))

        async def fake_repo_query(sql: str, params=None):
            return []

        import open_notebook.vectorstore.search as search_mod

        monkeypatch.setattr(search_mod, "repo_query", fake_repo_query)

        rows = await vector_search_qdrant([0.1], 10, False, True, 0.2)
        assert rows == []
        assert recorded == []  # no qdrant call when sources excluded
