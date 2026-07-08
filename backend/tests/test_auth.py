"""Auth boundaries: public reads open, writes admin-only."""

from .conftest import ADMIN_EMAIL, ADMIN_PASSWORD


class TestPublicReads:
    def test_leaderboard_open(self, client):
        response = client.get("/api/leaderboard")
        assert response.status_code == 200

    def test_prompts_open(self, client):
        assert client.get("/api/prompts").status_code == 200

    def test_agents_open(self, client):
        assert client.get("/api/agents").status_code == 200


class TestWriteBoundaries:
    def test_write_requires_token(self, client):
        assert client.post("/api/agents", json={"name": "X"}).status_code == 401
        assert client.post("/api/prompts", json={"name": "X", "text": "y"}).status_code == 401
        assert client.put("/api/settings", json={"default_cost_bps": 5}).status_code == 401
        assert client.delete("/api/prices/cache").status_code == 401

    def test_garbage_token_rejected(self, client):
        response = client.post("/api/agents", json={"name": "X"}, headers={"Authorization": "Bearer nope"})
        assert response.status_code == 401


class TestLogin:
    def test_login_and_me(self, client):
        response = client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert response.status_code == 200
        token = response.json()["token"]

        me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["email"] == ADMIN_EMAIL

    def test_wrong_password(self, client):
        response = client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong"})
        assert response.status_code == 401

    def test_password_change_roundtrip(self, client, admin_headers):
        response = client.put(
            "/api/auth/password",
            json={"current_password": ADMIN_PASSWORD, "new_password": "new-password-123"},
            headers=admin_headers,
        )
        assert response.status_code == 200

        assert (
            client.post(
                "/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/auth/login", json={"email": ADMIN_EMAIL, "password": "new-password-123"}
            ).status_code
            == 200
        )
