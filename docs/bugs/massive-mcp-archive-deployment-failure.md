# Massive MCP archive deployment failure

- Fixed: 2026-07-29 20:46:58 UTC (+0000)
- Commit before fix: `f9759679dea1e89122aeee1fd5299f4c900f9aa7`

## Symptom

The Coolify Nixpacks build failed while installing `requirements.txt` because GitHub returned HTTP
404 for the `mcp_massive` v0.10.0 tag archive.

## Confirmed root cause

The project installed `mcp_massive` from GitHub's redirecting tag-archive endpoint. The release and
tag were not missing: GitHub reported that v0.10.0 still points to commit
`c58ec7e4df7482c53fc4adeb2de0e979f77f3a23`, and the same archive endpoint returned HTTP 200 when
retested after the failed deployment. The deployment therefore encountered a transient archive
endpoint failure. Massive does not publish this package on PyPI.

## Changes

- Replace the redirecting tag URL with GitHub's direct codeload URL.
- Pin the package to the immutable v0.10.0 commit rather than a movable tag.
- Do not pin the generated archive's checksum: GitHub guarantees stable file contents for a commit
  archive, but its compression can change.
- Verify that pip downloads the commit archive, builds the package, and imports version 0.10.0 in
  a completely fresh virtual environment.
