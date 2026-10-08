"""The built-in demo page."""


def test_demo_page_is_served_at_root(client):
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Bulk Certificate Generator" in response.text
    # It talks to the same API the tests exercise.
    assert "/jobs" in response.text


def test_demo_page_is_not_part_of_the_openapi_schema(client):
    paths = client.get("/openapi.json").json()["paths"]

    assert "/" not in paths
    assert "/jobs" in paths
