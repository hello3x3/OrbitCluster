"""The guide is public and read-only; existing protected pages remain protected."""
from html.parser import HTMLParser

from test_terminal_api import portal  # reuse the fake cluster fixture


class Links(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.hrefs = []
        self.ids = set()
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and "href" in attrs:
            self.hrefs.append(attrs["href"])
        if "id" in attrs:
            self.ids.add(attrs["id"])


def test_public_guide_and_anchors_without_private_data(portal):
    client = portal.test_client()
    response = client.get("/guide")
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    links = Links(text)
    for href in links.hrefs:
        if href.startswith("#"):
            assert href[1:] in links.ids
    assert "/login" in links.hrefs
    assert "TestPass123" not in text
    assert "ssh-ed25519 AAAA" not in text
    assert client.get("/my").status_code == 302
    assert client.get("/").location.endswith("/login")
    assert client.post("/guide").status_code == 405


def test_login_and_authenticated_navigation_link_to_guide(portal):
    client = portal.test_client()
    assert "/guide" in Links(client.get("/login").get_data(as_text=True)).hrefs
    response = client.post("/login", data={"_csrf": _csrf(client), "username": "alice", "password": "TestPass123"})
    assert response.status_code == 302
    client.get("/my")  # Consume the normal one-time login notice before comparing session state.
    assert client.get("/").location.endswith("/my")
    business_links = Links(client.get("/my").get_data(as_text=True)).hrefs
    assert {"/my", "/images", "/apply", "/status", "/profile", "/guide"} <= set(business_links)
    before = dict(_session(client))
    assert client.get("/guide").status_code == 200
    assert dict(_session(client)) == before
    assert "/guide" in Links(client.get("/my").get_data(as_text=True)).hrefs


def _csrf(client):
    with client.session_transaction() as session:
        return session["csrf"]


def _session(client):
    with client.session_transaction() as session:
        return dict(session)
