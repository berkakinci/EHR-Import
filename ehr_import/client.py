"""
FHIR HTTP client — wraps authenticated requests, pagination, and attachment fetching.
"""

import base64

import requests


class FHIRClient:
    """Authenticated FHIR R4 client for a single patient session."""

    def __init__(self, base_url: str, token: str, provider_name: str, patient_id: str):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.provider_name = provider_name
        self.patient_id = patient_id

    def _headers(self, accept: str = "application/fhir+json") -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": accept,
        }

    def get(self, resource_path: str, params: dict = None) -> dict:
        """Make an authenticated GET request to a FHIR endpoint."""
        url = f"{self.base_url}/{resource_path}"
        resp = requests.get(url, headers=self._headers(), params=params, timeout=30)
        if resp.status_code == 401:
            raise PermissionError("Token expired — refresh needed")
        resp.raise_for_status()
        return resp.json()

    def get_all_pages(self, resource_path: str, params: dict = None) -> tuple[list, list]:
        """Follow FHIR pagination to get all results.

        Returns (entries, warnings) where warnings is a list of OperationOutcome issue dicts.
        If the initial request returns a 4xx with an OperationOutcome body, the issues
        are returned as warnings (no exception raised) so they get stored in pull_warnings.
        """
        entries = []
        warnings = []
        try:
            bundle = self.get(resource_path, params)
        except requests.exceptions.HTTPError as e:
            # Try to extract OperationOutcome from the error response
            try:
                body = e.response.json()
                if body.get("resourceType") == "OperationOutcome":
                    for issue in body.get("issue", []):
                        warnings.append(issue)
                    return entries, warnings
            except (ValueError, AttributeError):
                pass
            raise  # Re-raise if we couldn't parse an OperationOutcome

        while True:
            for entry in bundle.get("entry", []):
                resource = entry.get("resource", entry)
                if resource.get("resourceType") == "OperationOutcome":
                    for issue in resource.get("issue", []):
                        warnings.append(issue)
                    continue
                entries.append(resource)

            next_link = None
            for link in bundle.get("link", []):
                if link.get("relation") == "next":
                    next_link = link.get("url")
                    break
            if not next_link:
                break

            resp = requests.get(next_link, headers=self._headers(), timeout=30)
            resp.raise_for_status()
            bundle = resp.json()

        return entries, warnings

    def fetch_attachment(self, resource: dict, content_field: str
                         ) -> tuple[str | None, str, str | None, str | None]:
        """Fetch text content from a resource's attachment field.

        content_field is either "content" (DocumentReference) or "presentedForm" (DiagnosticReport).

        Returns (content_text, fetch_status, fetch_detail, fetch_url).
        """
        if content_field == "content":
            # DocumentReference: content[].attachment
            items = resource.get("content", [])
            attachments = [item.get("attachment", {}) for item in items]
        elif content_field == "presentedForm":
            # DiagnosticReport: presentedForm[]
            attachments = resource.get("presentedForm", [])
        else:
            return None, "no_attachment", None, None

        if not attachments:
            return None, "no_attachment", None, None

        for attachment in attachments:
            # Inline base64 data
            if "data" in attachment:
                decoded = base64.b64decode(attachment["data"])
                text = decoded.decode("utf-8", errors="replace")
                if text.strip():
                    return text, "ok", None, None
                else:
                    return None, "empty", "Inline data decoded but was empty/whitespace", None

            # URL reference — fetch Binary
            if "url" in attachment:
                fetch_url = attachment["url"]
                if not fetch_url.startswith("http"):
                    fetch_url = f"{self.base_url}/{fetch_url}"
                try:
                    accept = attachment.get("contentType", "text/plain")
                    resp = requests.get(
                        fetch_url, headers=self._headers(accept=accept), timeout=30
                    )
                    if resp.status_code == 200 and resp.text.strip():
                        return resp.text, "ok", None, None
                    elif resp.status_code == 200:
                        return None, "empty", "Binary fetched OK but body was empty", fetch_url
                    else:
                        error_body = _extract_error_body(resp)
                        detail = f"HTTP {resp.status_code}"
                        if error_body:
                            detail += f" — {error_body}"
                        return None, "fetch_failed", detail, fetch_url
                except requests.RequestException as e:
                    return None, "fetch_failed", f"Request error: {e}", fetch_url

        return None, "no_attachment", "Attachments present but no data or url fields found", None


def _extract_error_body(resp) -> str:
    """Try to extract a useful error message from a failed response."""
    try:
        outcome = resp.json()
        issues = outcome.get("issue", [])
        if issues:
            return "; ".join(
                i.get("diagnostics", i.get("details", {}).get("text", ""))
                for i in issues if i.get("diagnostics") or i.get("details")
            )
    except (ValueError, AttributeError):
        pass
    body = resp.text.strip()
    return body[:200] if body else ""
