"""Minimal GitHub REST API client."""

import requests

GITHUB_API = "https://api.github.com"


class GitHub:
    def __init__(self, token):
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )

    def get(self, path, **params):
        return self._request("GET", path, params=params)

    def post(self, path, body):
        return self._request("POST", path, json=body)

    def patch(self, path, body):
        return self._request("PATCH", path, json=body)

    def delete(self, path):
        return self._request("DELETE", path)

    def _request(self, method, path, **kwargs):
        response = self._session.request(
            method, f"{GITHUB_API}/{path}", timeout=30, **kwargs
        )
        response.raise_for_status()
        return response.json() if response.content else None
