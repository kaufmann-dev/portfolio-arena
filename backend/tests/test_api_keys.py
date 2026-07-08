"""API-key management: JWT-admin only, plaintext shown once, revocation."""


class TestApiKeyManagement:
    def test_create_returns_plaintext_once(self, client, admin_headers):
        response = client.post("/api/keys", json={"name": "rebalancer"}, headers=admin_headers)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["name"] == "rebalancer"
        assert body["key"].startswith("arena_")
        assert body["prefix"] and body["key"].startswith(body["prefix"])
        assert body["revoked"] is False

        # The plaintext is never returned again by the list endpoint.
        listed = client.get("/api/keys", headers=admin_headers).json()["keys"]
        assert len(listed) == 1
        assert "key" not in listed[0]
        assert listed[0]["prefix"] == body["prefix"]

    def test_revoke(self, client, admin_headers):
        key_id = client.post("/api/keys", json={"name": "temp"}, headers=admin_headers).json()["id"]
        assert client.delete(f"/api/keys/{key_id}", headers=admin_headers).status_code == 200

        listed = client.get("/api/keys", headers=admin_headers).json()["keys"]
        assert listed[0]["revoked"] is True


class TestApiKeyAuthBoundaries:
    def test_management_requires_jwt(self, client):
        assert client.get("/api/keys").status_code == 401
        assert client.post("/api/keys", json={"name": "x"}).status_code == 401

    def test_api_key_does_not_authenticate_rest_admin(self, client, api_key):
        """An MCP API key must not be usable as a bearer token on REST admin routes."""
        headers = {"Authorization": f"Bearer {api_key}"}
        assert client.post("/api/agents", json={"name": "X"}, headers=headers).status_code == 401
        assert client.get("/api/keys", headers=headers).status_code == 401
