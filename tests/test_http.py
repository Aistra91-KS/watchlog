"""Tests du client HTTP commun : cache, cache de secours, backoff, RateLimiter, download."""

from datetime import timedelta

import httpx
import pytest

from helpers_http import FakeApi, age_cache, cache_keys, make_client, make_test_engine
from watchlog.core.http import ApiError, RateLimiter


@pytest.fixture
def engine(tmp_path):
    return make_test_engine(tmp_path)


def ok(request):
    """Réponse 200 qui renvoie le chemin demandé (pratique pour vérifier)."""
    return httpx.Response(200, json={"path": request.url.path})


# --- cache ------------------------------------------------------------------

def test_second_call_served_from_cache(engine):
    api = FakeApi(ok)
    client = make_client(engine, api)

    first = client.get_json("/shows/1", ttl=timedelta(days=1))
    second = client.get_json("/shows/1", ttl=timedelta(days=1))

    assert first == second == {"path": "/shows/1"}
    assert len(api.requests) == 1  # le 2e appel n'a pas touché le réseau


def test_different_params_are_cached_separately(engine):
    api = FakeApi(ok)
    client = make_client(engine, api)

    client.get_json("/search", {"q": "a"}, ttl=timedelta(days=1))
    client.get_json("/search", {"q": "b"}, ttl=timedelta(days=1))
    client.get_json("/search", {"q": "a"}, ttl=timedelta(days=1))

    assert len(api.requests) == 2


def test_expired_cache_triggers_new_request(engine):
    api = FakeApi(ok)
    client = make_client(engine, api)

    client.get_json("/shows/1", ttl=timedelta(days=1))
    age_cache(engine, timedelta(days=2))
    client.get_json("/shows/1", ttl=timedelta(days=1))

    assert len(api.requests) == 2


def test_ttl_callable_uses_cached_data(engine):
    statuses = {"/shows/1": "Ended", "/shows/2": "Running"}
    api = FakeApi(lambda r: httpx.Response(200, json={"status": statuses[r.url.path]}))
    client = make_client(engine, api)

    def ttl(data):  # 7 jours si terminée, 1 jour sinon
        return timedelta(days=7) if data["status"] == "Ended" else timedelta(days=1)

    client.get_json("/shows/1", ttl=ttl)
    client.get_json("/shows/2", ttl=ttl)
    age_cache(engine, timedelta(days=3))
    client.get_json("/shows/1", ttl=ttl)   # encore valide (7 j)
    client.get_json("/shows/2", ttl=ttl)   # expirée (1 j)

    assert api.paths == ["/shows/1", "/shows/2", "/shows/2"]


def test_force_ignores_cache_but_updates_it(engine):
    counter = {"n": 0}

    def handler(request):
        counter["n"] += 1
        return httpx.Response(200, json={"version": counter["n"]})

    api = FakeApi(handler)
    client = make_client(engine, api)

    assert client.get_json("/shows/1", ttl=timedelta(days=1)) == {"version": 1}
    assert client.get_json("/shows/1", ttl=timedelta(days=1), force=True) == {"version": 2}
    # Le cache contient maintenant la version 2
    assert client.get_json("/shows/1", ttl=timedelta(days=1)) == {"version": 2}
    assert len(api.requests) == 2


def test_ttl_none_means_no_cache(engine):
    api = FakeApi(ok)
    client = make_client(engine, api)

    client.get_json("/updates/shows")
    client.get_json("/updates/shows")

    assert len(api.requests) == 2
    assert cache_keys(engine) == []


# --- cache de secours quand l'API tombe ---------------------------------------

def test_stale_cache_used_when_network_fails(engine):
    state = {"down": False}

    def handler(request):
        if state["down"]:
            raise httpx.ConnectError("connexion impossible")
        return httpx.Response(200, json={"name": "Ancienne réponse"})

    delays = []
    client = make_client(engine, FakeApi(handler), delays)
    client.get_json("/shows/1", ttl=timedelta(days=1))

    state["down"] = True
    age_cache(engine, timedelta(days=30))  # cache largement expiré
    assert client.get_json("/shows/1", ttl=timedelta(days=1)) == {"name": "Ancienne réponse"}
    assert delays == [1, 2, 4, 8]  # 5 essais, backoff exponentiel entre chacun


def test_stale_cache_used_after_repeated_5xx(engine):
    state = {"down": False}

    def handler(request):
        if state["down"]:
            return httpx.Response(503)
        return httpx.Response(200, json={"name": "Ancienne réponse"})

    api = FakeApi(handler)
    client = make_client(engine, api, [])
    client.get_json("/shows/1", ttl=timedelta(days=1))

    state["down"] = True
    age_cache(engine, timedelta(days=30))
    assert client.get_json("/shows/1", ttl=timedelta(days=1)) == {"name": "Ancienne réponse"}
    assert len(api.requests) == 1 + 5  # 1 succès puis 5 essais ratés


def test_api_down_without_cache_raises_api_error(engine):
    api = FakeApi(lambda r: httpx.Response(500))
    client = make_client(engine, api, [], max_retries=2)

    with pytest.raises(ApiError, match="Test indisponible"):
        client.get_json("/shows/1", ttl=timedelta(days=1))
    assert len(api.requests) == 3


def test_network_error_without_cache_raises_api_error(engine):
    def handler(request):
        raise httpx.ReadTimeout("trop long")

    client = make_client(engine, FakeApi(handler), [], max_retries=1)
    with pytest.raises(ApiError, match="erreur réseau"):
        client.get_json("/shows/1")


# --- codes HTTP ---------------------------------------------------------------

def test_429_backoff_respects_retry_after_then_succeeds(engine):
    responses = [
        httpx.Response(429, headers={"Retry-After": "5"}),
        httpx.Response(429),  # sans Retry-After : délai exponentiel
        httpx.Response(200, json={"ok": True}),
    ]
    api = FakeApi(lambda r: responses.pop(0))
    delays = []
    client = make_client(engine, api, delays)

    assert client.get_json("/shows/1") == {"ok": True}
    assert delays == [5, 2]  # max(1, 5) puis 2 ** 1
    assert len(api.requests) == 3


def test_429_retry_after_shorter_than_backoff(engine):
    responses = [httpx.Response(429), httpx.Response(429, headers={"Retry-After": "1"}),
                 httpx.Response(429), httpx.Response(200, json={})]
    delays = []
    client = make_client(engine, FakeApi(lambda r: responses.pop(0)), delays)

    client.get_json("/x")
    assert delays == [1, 2, 4]  # on garde le plus long des deux délais


def test_429_retry_after_as_http_date_does_not_crash(engine):
    responses = [httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
                 httpx.Response(200, json={"ok": True})]
    delays = []
    client = make_client(engine, FakeApi(lambda r: responses.pop(0)), delays)

    assert client.get_json("/x") == {"ok": True}
    assert delays == [1]


def test_404_returns_none_and_is_not_cached(engine):
    api = FakeApi(lambda r: httpx.Response(404))
    client = make_client(engine, api)

    assert client.get_json("/shows/999", ttl=timedelta(days=1)) is None
    assert cache_keys(engine) == []


def test_unexpected_status_raises_api_error(engine):
    api = FakeApi(lambda r: httpx.Response(401))
    client = make_client(engine, api, [])

    with pytest.raises(ApiError, match="HTTP 401"):
        client.get_json("/shows/1")
    assert len(api.requests) == 1  # pas de réessai sur une erreur 4xx


# --- secrets ------------------------------------------------------------------

def test_cache_key_never_contains_api_key(engine):
    api = FakeApi(ok)
    client = make_client(engine, api, auth_params={"api_key": "fake-key"})

    client.get_json("/movie/1", {"language": "fr-FR"}, ttl=timedelta(days=1))

    # La clé est bien envoyée à l'API...
    assert api.requests[0].url.params["api_key"] == "fake-key"
    assert api.requests[0].url.params["language"] == "fr-FR"
    # ... mais n'apparaît jamais dans le cache.
    assert cache_keys(engine) == ["/movie/1?language=fr-FR"]
    assert "fake-key" not in client.cache_key("/movie/1", {"language": "fr-FR"})


def test_api_error_message_has_no_secret(engine):
    def handler(request):
        raise httpx.ConnectError(f"impossible de joindre {request.url}")

    client = make_client(engine, FakeApi(handler), [], auth_params={"api_key": "fake-key"},
                         max_retries=0)
    with pytest.raises(ApiError) as error:
        client.get_json("/movie/1")
    assert "fake-key" not in str(error.value)


# --- RateLimiter --------------------------------------------------------------

class FakeClock:
    """Horloge factice : le temps n'avance que quand on « dort »."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_rate_limiter_no_wait_under_limit():
    clock = FakeClock()
    limiter = RateLimiter(3, 10, clock=clock, sleep=clock.sleep)

    for _ in range(3):
        limiter.wait()
    assert clock.sleeps == []


def test_rate_limiter_waits_for_oldest_call_to_expire():
    clock = FakeClock()
    limiter = RateLimiter(2, 10, clock=clock, sleep=clock.sleep)

    limiter.wait()          # t = 0
    clock.now = 4
    limiter.wait()          # t = 4
    clock.now = 6
    limiter.wait()          # 3e appel : il faut attendre t = 10 (0 + 10)
    assert clock.sleeps == [4]
    assert clock.now == 10


def test_rate_limiter_window_slides():
    clock = FakeClock()
    limiter = RateLimiter(2, 10, clock=clock, sleep=clock.sleep)

    limiter.wait()
    limiter.wait()
    clock.now = 15          # les deux appels sont sortis de la fenêtre
    limiter.wait()
    limiter.wait()
    assert clock.sleeps == []


# --- download -----------------------------------------------------------------

def test_download_writes_file_only_once(engine, tmp_path):
    api = FakeApi(lambda r: httpx.Response(200, content=b"image-bytes"))
    client = make_client(engine, api)
    dest = tmp_path / "posters" / "a.jpg"

    assert client.download("https://images.example.test/a.jpg", dest) is True
    assert client.download("https://images.example.test/a.jpg", dest) is True

    assert dest.read_bytes() == b"image-bytes"
    assert len(api.requests) == 1


def test_download_failure_returns_false(engine, tmp_path):
    api = FakeApi(lambda r: httpx.Response(404))
    client = make_client(engine, api)
    dest = tmp_path / "posters" / "absent.jpg"

    assert client.download("https://images.example.test/absent.jpg", dest) is False
    assert not dest.exists()


# --- échecs signalés (pas de repli silencieux) -----------------------------------

def test_force_does_not_fall_back_on_stale_cache(engine):
    """Synchro (force=True) : une panne doit se voir, pas relire l'ancienne réponse."""
    state = {"down": False}

    def handler(request):
        if state["down"]:
            raise httpx.ConnectError("connexion impossible")
        return httpx.Response(200, json={"name": "Ancienne réponse"})

    client = make_client(engine, FakeApi(handler))
    client.get_json("/shows/1", ttl=timedelta(days=1))
    state["down"] = True

    with pytest.raises(ApiError):
        client.get_json("/shows/1", ttl=timedelta(days=1), force=True)
    assert client.get_json("/shows/1", ttl=timedelta(days=1)) == {"name": "Ancienne réponse"}


def test_unexpected_status_is_logged(engine, caplog):
    client = make_client(engine, FakeApi(lambda request: httpx.Response(401)))
    with pytest.raises(ApiError, match="HTTP 401"):
        client.get_json("/shows/1")
    assert "HTTP 401" in caplog.text


def test_invalid_json_raises_api_error(engine):
    client = make_client(engine, FakeApi(lambda request: httpx.Response(200, text="<html>")))
    with pytest.raises(ApiError, match="illisible"):
        client.get_json("/shows/1")
