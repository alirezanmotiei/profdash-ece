import pytest
from starlette.testclient import TestClient
from profdash.dashboard.app import app


def test_openalex_search_empty():
    client = TestClient(app)
    # Search with empty query
    res = client.get("/api/openalex/search?q=")
    assert res.status_code == 200
    assert res.json() == {"results": []}
