"""Tests du client TMDB. Faux identifiants uniquement, jamais le vrai .env."""

from datetime import date, timedelta

import httpx
import pytest

from helpers_http import FakeApi, load_fixture, make_test_engine, no_sleep
from watchlog.core.tmdb import TMDBClient, extract_release_dates, movie_ttl

FAKE_TOKEN = "fake-token"
FAKE_KEY = "fake-key"


@pytest.fixture
def engine(tmp_path):
    return make_test_engine(tmp_path)


def tmdb_server(request):
    """Faux TMDB : /authentication et /movie/693134, 404 pour le reste."""
    if request.url.path == "/3/authentication":
        return httpx.Response(200, json=load_fixture("tmdb_authentication.json"))
    if request.url.path == "/3/movie/693134":
        return httpx.Response(200, json=load_fixture("tmdb_movie.json"))
    if request.url.host == "image.tmdb.org":
        return httpx.Response(200, content=b"poster")
    return httpx.Response(404)


def make_tmdb(engine, api, token=FAKE_TOKEN, api_key=""):
    # Identifiants passés explicitement : le .env n'est jamais consulté.
    return TMDBClient(engine, token=token, api_key=api_key, transport=api.transport,
                      sleep=no_sleep)


# --- authentification ---------------------------------------------------------

def test_bearer_header_when_token_present(engine):
    api = FakeApi(tmdb_server)
    client = make_tmdb(engine, api, token=FAKE_TOKEN, api_key=FAKE_KEY)

    client.check_connection()
    client.get_movie(693134)

    assert len(api.requests) == 2
    for request in api.requests:
        assert request.headers["Authorization"] == "Bearer fake-token"
        assert "api_key" not in request.url.params
        assert FAKE_KEY not in str(request.url)


def test_api_key_param_when_token_empty(engine):
    api = FakeApi(tmdb_server)
    client = make_tmdb(engine, api, token="", api_key=FAKE_KEY)

    client.check_connection()

    request = api.requests[0]
    assert request.url.params["api_key"] == "fake-key"
    assert "Authorization" not in request.headers


# --- check_connection ---------------------------------------------------------

def test_check_connection_not_configured(engine):
    api = FakeApi(tmdb_server)
    client = make_tmdb(engine, api, token="", api_key="")

    connected, message = client.check_connection()

    assert connected is False
    assert "identifiants absents" in message
    assert api.requests == []  # aucun appel réseau inutile


def test_check_connection_success(engine):
    client = make_tmdb(engine, FakeApi(tmdb_server))

    connected, message = client.check_connection()

    assert connected is True
    assert message == "TMDB connecté."
    assert client.available is True


@pytest.mark.parametrize("token, api_key", [(FAKE_TOKEN, ""), ("", FAKE_KEY)])
def test_check_connection_refused_401(engine, token, api_key):
    api = FakeApi(lambda r: httpx.Response(401, json=load_fixture("tmdb_401.json")))
    client = make_tmdb(engine, api, token=token, api_key=api_key)

    connected, message = client.check_connection()

    assert connected is False
    assert "identifiants refusés" in message
    assert FAKE_TOKEN not in message and FAKE_KEY not in message


def test_check_connection_not_json(engine):
    """Portail Wi-Fi : page HTML en 200. Message clair, pas de ValueError."""
    api = FakeApi(lambda r: httpx.Response(200, text="<html>Connexion au Wi-Fi</html>"))
    client = make_tmdb(engine, api)

    connected, message = client.check_connection()

    assert connected is False
    assert "réponse illisible" in message
    assert client.available is False


def test_check_connection_unreachable(engine):
    def handler(request):
        raise httpx.ConnectError("réseau coupé")

    client = make_tmdb(engine, FakeApi(handler))

    connected, message = client.check_connection()

    assert connected is False
    assert "injoignable" in message
    assert FAKE_TOKEN not in message


def test_connection_check_done_only_once(engine):
    api = FakeApi(tmdb_server)
    client = make_tmdb(engine, api)

    assert client.available and client.available
    assert api.paths == ["/3/authentication"]


# --- dates de sortie ----------------------------------------------------------

def test_extract_release_dates_from_fixture():
    movie = load_fixture("tmdb_movie.json")

    fr, original = extract_release_dates(movie)

    # FR : avant-première (1), séances limitées (2), DVD (5) et TV (6) ignorés,
    # sortie belge ignorée -> la plus ancienne des sorties FR cinéma (3) / numérique (4).
    assert fr == date(2024, 2, 28)
    assert original == date(2024, 2, 27)


def test_extract_release_dates_takes_earliest_fr_type_3_or_4():
    movie = {"release_date": "2023-05-01", "release_dates": {"results": [
        {"iso_3166_1": "FR", "release_dates": [
            {"type": 3, "release_date": "2023-09-01T00:00:00.000Z"},
            {"type": 4, "release_date": "2023-07-15T00:00:00.000Z"},  # sortie directe en VOD
        ]},
    ]}}
    assert extract_release_dates(movie) == (date(2023, 7, 15), date(2023, 5, 1))


def test_extract_release_dates_no_fr_cinema_or_digital():
    movie = {"release_date": "2023-05-01", "release_dates": {"results": [
        {"iso_3166_1": "FR", "release_dates": [
            {"type": 1, "release_date": "2023-04-01T00:00:00.000Z"},
            {"type": 2, "release_date": "2023-04-02T00:00:00.000Z"},
            {"type": 5, "release_date": "2023-10-01T00:00:00.000Z"},
            {"type": 6, "release_date": "2024-01-01T00:00:00.000Z"},
        ]},
        {"iso_3166_1": "US", "release_dates": [
            {"type": 3, "release_date": "2023-05-01T00:00:00.000Z"},
        ]},
    ]}}
    assert extract_release_dates(movie) == (None, date(2023, 5, 1))


def test_extract_release_dates_without_release_dates():
    assert extract_release_dates({"release_date": ""}) == (None, None)
    assert extract_release_dates({}) == (None, None)


# --- durée du cache ----------------------------------------------------------

def test_movie_ttl_released_movie():
    assert movie_ttl(load_fixture("tmdb_movie.json")) == timedelta(days=30)


def test_movie_ttl_upcoming_movie():
    later = (date.today() + timedelta(days=60)).isoformat()
    assert movie_ttl({"release_date": later}) == timedelta(days=1)


def test_movie_ttl_without_any_date():
    assert movie_ttl({}) == timedelta(days=1)


# --- fiches films -------------------------------------------------------------

def test_get_movie_returns_french_data_and_uses_cache(engine):
    api = FakeApi(tmdb_server)
    client = make_tmdb(engine, api)

    movie = client.get_movie(693134)
    again = client.get_movie(693134)

    assert movie["title"] == "Dune : Deuxième partie"
    assert movie["original_title"] == "Dune: Part Two"
    assert movie["runtime"] == 167
    assert again == movie
    movie_requests = [r for r in api.requests if r.url.path == "/3/movie/693134"]
    assert len(movie_requests) == 1
    assert movie_requests[0].url.params["language"] == "fr-FR"
    assert movie_requests[0].url.params["append_to_response"] == "release_dates"


def test_get_movie_unknown_returns_none(engine):
    client = make_tmdb(engine, FakeApi(tmdb_server))
    assert client.get_movie(1) is None


def test_get_movie_returns_none_when_tmdb_unavailable(engine):
    def handler(request):
        raise httpx.ConnectError("réseau coupé")

    api = FakeApi(handler)
    client = make_tmdb(engine, api)

    assert client.get_movie(693134) is None
    assert client.search_movies("Dune") == []
    # Seul le test de connexion a été tenté, jamais /movie
    assert all(path == "/3/authentication" for path in api.paths)


def test_get_movie_returns_none_when_not_configured(engine):
    api = FakeApi(tmdb_server)
    client = make_tmdb(engine, api, token="", api_key="")

    assert client.get_movie(693134) is None
    assert api.requests == []


# --- affiches -----------------------------------------------------------------

def test_poster_url():
    assert TMDBClient.poster_url("/abc.jpg") == "https://image.tmdb.org/t/p/w342/abc.jpg"
    assert TMDBClient.poster_url(None) is None


def test_poster_downloaded_without_authorization_header(engine, tmp_path):
    api = FakeApi(tmdb_server)
    client = make_tmdb(engine, api)
    poster = load_fixture("tmdb_movie.json")["poster_path"]
    dest = tmp_path / "posters" / "693134.jpg"

    assert client.download(TMDBClient.poster_url(poster), dest) is True

    request = api.requests[0]
    assert request.url.host == "image.tmdb.org"
    assert "Authorization" not in request.headers
    assert dest.read_bytes() == b"poster"


# --- saisons de série -----------------------------------------------------------

def test_tv_season_reads_and_caches(engine):
    season = {"episodes": [{"episode_number": 1, "air_date": "2006-09-25", "runtime": 43}]}

    def server(request):
        if request.url.path == "/3/authentication":
            return httpx.Response(200, json=load_fixture("tmdb_authentication.json"))
        if request.url.path == "/3/tv/1639/season/1":
            return httpx.Response(200, json=season)
        return httpx.Response(404)

    api = FakeApi(server)
    client = make_tmdb(engine, api)

    assert client.tv_season(1639, 1) == season
    assert client.tv_season(1639, 1) == season       # deuxième appel : cache
    assert client.tv_season(1639, 9) is None         # saison inconnue
    assert api.paths.count("/3/tv/1639/season/1") == 1
    assert api.requests[-1].url.params["language"] == "fr-FR"   # titres d'épisodes traduits
