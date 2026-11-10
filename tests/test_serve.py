from app import serve
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware


def test_forwarded_ips_default_to_loopback(monkeypatch):
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    options = serve.server_options()
    assert options["proxy_headers"] is True
    assert options["forwarded_allow_ips"] == "127.0.0.1"


def test_forwarded_ips_follow_environment(monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    assert serve.server_options()["forwarded_allow_ips"] == "*"


def client_host_app(trusted):
    app = FastAPI()

    @app.get("/ip")
    def ip(request: Request):
        return {"ip": request.client.host}

    return TestClient(ProxyHeadersMiddleware(app, trusted_hosts=trusted))


def test_trusted_proxy_exposes_distinct_clients():
    # Login throttling keys on request.client.host; behind a trusted proxy two
    # visitors must not share the proxy's address (one shared bucket = lockout for all).
    client = client_host_app("*")
    first = client.get("/ip", headers={"X-Forwarded-For": "203.0.113.7"}).json()["ip"]
    second = client.get("/ip", headers={"X-Forwarded-For": "198.51.100.9"}).json()["ip"]
    assert (first, second) == ("203.0.113.7", "198.51.100.9")


def test_untrusted_peer_cannot_spoof_client_ip():
    client = client_host_app("127.0.0.1")
    ip = client.get("/ip", headers={"X-Forwarded-For": "203.0.113.7"}).json()["ip"]
    assert ip != "203.0.113.7"
