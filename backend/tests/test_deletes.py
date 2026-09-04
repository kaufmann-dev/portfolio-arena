"""Delete endpoints for agents and portfolios plus prompt archival behavior."""


class TestDeleteAgent:
    def test_delete_unused_agent(self, client, admin_headers, sample_agent):
        response = client.delete(f"/api/agents/{sample_agent['id']}", headers=admin_headers)
        assert response.status_code == 200, response.text
        listing = client.get("/api/agents").json()["agents"]
        assert all(a["id"] != sample_agent["id"] for a in listing)

    def test_delete_agent_in_use_blocked(self, client, admin_headers, sample_portfolio, sample_agent):
        response = client.delete(f"/api/agents/{sample_agent['id']}", headers=admin_headers)
        assert response.status_code == 409
        assert "portfolio" in response.json()["detail"].lower()

    def test_delete_missing_agent(self, client, admin_headers):
        assert client.delete("/api/agents/999999", headers=admin_headers).status_code == 404


class TestArchiveAgent:
    def test_archive_agent_with_active_portfolio_is_blocked(
        self,
        client,
        admin_headers,
        sample_agent,
        sample_portfolio,
    ):
        response = client.post(
            f"/api/admin/agents/{sample_agent['id']}/archive",
            headers=admin_headers,
        )

        assert response.status_code == 409
        assert response.json()["detail"] == ("1 active portfolio(s) must be archived or reassigned first.")

    def test_archive_preserves_archived_portfolio_and_explains_delete_blocker(
        self,
        client,
        admin_headers,
        sample_agent,
        sample_portfolio,
    ):
        archived_portfolio = client.patch(
            f"/api/portfolios/{sample_portfolio['id']}",
            json={"status": "archived"},
            headers=admin_headers,
        )
        assert archived_portfolio.status_code == 200, archived_portfolio.text

        response = client.post(
            f"/api/admin/agents/{sample_agent['id']}/archive",
            headers=admin_headers,
        )

        assert response.status_code == 200, response.text
        archived = response.json()
        assert archived["status"] == "archived"
        assert archived["active_portfolio_count"] == 0
        assert archived["archived_portfolio_count"] == 1
        assert archived["can_delete"] is False
        assert archived["delete_blocker"] == (
            "Cannot permanently delete: 1 archived portfolio(s) preserve history."
        )
        assert all(agent["id"] != sample_agent["id"] for agent in client.get("/api/agents").json()["agents"])
        detail = client.get(f"/api/agents/{sample_agent['slug']}")
        assert detail.status_code == 200
        assert detail.json()["agent"]["status"] == "archived"

    def test_archived_agent_can_be_replaced_but_not_restored_while_duplicate_is_active(
        self,
        client,
        admin_headers,
        sample_agent,
        sample_model,
    ):
        archived = client.post(
            f"/api/admin/agents/{sample_agent['id']}/archive",
            headers=admin_headers,
        )
        assert archived.status_code == 200, archived.text

        replacement = client.post(
            "/api/agents",
            json={
                "model_id": sample_model["id"],
                "harness": "codex",
                "reasoning_effort": "xhigh",
            },
            headers=admin_headers,
        )
        assert replacement.status_code == 201, replacement.text

        archived_listing = client.get(
            "/api/admin/agents?status=archived",
            headers=admin_headers,
        ).json()["agents"]
        row = next(agent for agent in archived_listing if agent["id"] == sample_agent["id"])
        assert row["can_restore"] is False
        assert row["restore_blocker"].startswith("An active agent already uses")

        restore = client.post(
            f"/api/admin/agents/{sample_agent['id']}/unarchive",
            headers=admin_headers,
        )
        assert restore.status_code == 409
        assert "active agent with this execution profile" in restore.json()["detail"]

    def test_archived_agent_cannot_be_assigned_or_restore_an_archived_portfolio(
        self,
        client,
        admin_headers,
        sample_agent,
        sample_portfolio,
        sample_prompt,
    ):
        client.patch(
            f"/api/portfolios/{sample_portfolio['id']}",
            json={"status": "archived"},
            headers=admin_headers,
        )
        client.post(
            f"/api/admin/agents/{sample_agent['id']}/archive",
            headers=admin_headers,
        )

        created = client.post(
            "/api/portfolios",
            json={
                "name": "Should not exist",
                "agent_id": sample_agent["id"],
                "prompt_id": sample_prompt["id"],
                "prompt_mode": "managed",
                "direction": "long",
            },
            headers=admin_headers,
        )
        assert created.status_code == 422
        assert created.json()["detail"] == "Archived agents cannot be assigned to portfolios"

        restored_portfolio = client.patch(
            f"/api/portfolios/{sample_portfolio['id']}",
            json={"status": "active"},
            headers=admin_headers,
        )
        assert restored_portfolio.status_code == 409
        assert restored_portfolio.json()["detail"] == (
            "Choose an active agent before unarchiving this portfolio"
        )

    def test_unarchive_agent(self, client, admin_headers, sample_agent):
        client.post(
            f"/api/admin/agents/{sample_agent['id']}/archive",
            headers=admin_headers,
        )

        response = client.post(
            f"/api/admin/agents/{sample_agent['id']}/unarchive",
            headers=admin_headers,
        )

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "active"
        assert response.json()["archived_at"] is None

    def test_archive_missing_agent(self, client, admin_headers):
        response = client.post("/api/admin/agents/999999/archive", headers=admin_headers)
        assert response.status_code == 404


class TestArchivePrompt:
    def test_archive_unused_prompt_preserves_history(self, client, admin_headers, sample_prompt):
        response = client.post(
            f"/api/admin/prompts/{sample_prompt['id']}/archive",
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        listing = client.get("/api/admin/prompts?status=all", headers=admin_headers).json()["prompts"]
        archived = next(prompt for prompt in listing if prompt["id"] == sample_prompt["id"])
        assert archived["status"] == "archived"
        assert all(
            prompt["id"] != sample_prompt["id"] for prompt in client.get("/api/prompts").json()["prompts"]
        )
        history = client.get(
            f"/api/admin/prompts/{sample_prompt['id']}/versions",
            headers=admin_headers,
        ).json()
        assert [version["version"] for version in history["versions"]] == [1]

    def test_archive_prompt_in_use_blocked(self, client, admin_headers, sample_portfolio, sample_prompt):
        # sample_portfolio uses sample_prompt as its fixed prompt.
        response = client.post(
            f"/api/admin/prompts/{sample_prompt['id']}/archive",
            headers=admin_headers,
        )
        assert response.status_code == 409
        assert response.json()["detail"] == (
            "Archive every portfolio using this prompt before archiving the prompt."
        )

    def test_archive_prompt_preserves_archived_portfolio_history(
        self,
        client,
        admin_headers,
        sample_portfolio,
        sample_prompt,
    ):
        archived_portfolio = client.patch(
            f"/api/portfolios/{sample_portfolio['id']}",
            json={"status": "archived"},
            headers=admin_headers,
        )
        assert archived_portfolio.status_code == 200, archived_portfolio.text

        response = client.post(
            f"/api/admin/prompts/{sample_prompt['id']}/archive",
            headers=admin_headers,
        )

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "archived"
        assert response.json()["portfolio_count"] == 1

        detail = client.get(f"/api/portfolios/{sample_portfolio['slug']}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["portfolio"]["status"] == "archived"
        assert detail.json()["portfolio"]["prompt"]["id"] == sample_prompt["id"]

    def test_archive_missing_prompt(self, client, admin_headers):
        assert client.post("/api/admin/prompts/999999/archive", headers=admin_headers).status_code == 404


class TestDeletePortfolio:
    def test_delete_portfolio_cascades(self, client, admin_headers, sample_portfolio):
        response = client.delete(f"/api/portfolios/{sample_portfolio['id']}", headers=admin_headers)
        assert response.status_code == 200, response.text
        assert client.get(f"/api/portfolios/{sample_portfolio['slug']}").status_code == 404
        rows = client.get("/api/arena/managed?direction=long").json()["portfolios"]
        assert all(p["id"] != sample_portfolio["id"] for p in rows)

    def test_delete_last_portfolio_leaves_only_synthetic_spy(
        self,
        client,
        admin_headers,
        sample_portfolio,
    ):
        from .util import backdate_allocation

        backdate_allocation(sample_portfolio["allocation"]["id"], days_back=45)
        seeded = client.get("/api/arena/managed?direction=long").json()["portfolios"]
        assert seeded[0]["kind"] == "benchmark"
        assert seeded[0]["metrics"]["has_data"] is True

        response = client.delete(f"/api/portfolios/{sample_portfolio['id']}", headers=admin_headers)
        assert response.status_code == 200, response.text

        rows = client.get("/api/arena/managed?direction=long").json()["portfolios"]
        assert len(rows) == 1
        assert rows[0]["kind"] == "benchmark"
        assert rows[0]["id"] is None
        assert rows[0]["metrics"]["has_data"] is False

    def test_synthetic_spy_is_not_an_addressable_portfolio(self, client, admin_headers):
        assert client.get("/api/portfolios/spy").status_code == 404
        assert client.delete("/api/portfolios/spy", headers=admin_headers).status_code == 422

    def test_delete_missing_portfolio(self, client, admin_headers):
        assert client.delete("/api/portfolios/999999", headers=admin_headers).status_code == 404
