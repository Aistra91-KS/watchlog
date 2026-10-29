"""Tests des suggestions (recommandations TMDB pondérées par la bibliothèque)."""

import httpx
import pytest
from sqlmodel import Session, select

from conftest import FakeTMDB, FakeTVMaze
from helpers_db import add_movie, add_show
from helpers_http import FakeApi, cache_keys, no_sleep
from watchlog.core import library, suggestions
from watchlog.core.http import ApiError
from watchlog.core.models import (COMPLETED, FOR_LATER, MOVIE, SHOW, STOPPED, TO_WATCH, WATCHING,
                                  DismissedSuggestion, Show)
from watchlog.core.tmdb import TMDBClient


KEYWORD_IDS = {"dystopia": 4565, "superhero": 9715, "robot": 14544, "magic": 2343,
               "survival": 10349, "sequel": 9663}


def rec(tmdb_id, title, votes=100, genres=(18,), vote=7.5, tv=False):
    """Un titre recommandé, au format TMDB (séries : name / first_air_date)."""
    data = {"id": tmdb_id, "vote_count": votes, "vote_average": vote, "genre_ids": list(genres),
            "overview": f"Résumé de {title}", "poster_path": f"/p{tmdb_id}.jpg"}
    if tv:
        data.update(name=title, original_name=title, first_air_date="2020-01-01")
    else:
        data.update(title=title, original_title=title, release_date="2019-05-01")
    return data


class RecoTMDB(FakeTMDB):
    """Faux TMDB : recommandations, genres et ids externes fournis par le test."""

    def __init__(self, recos=None, external=None, find=None, fail_ids=(), keywords=None,
                 discover=None, trending_titles=None, **kwargs):
        super().__init__(**kwargs)
        self.recos = recos or {}          # (media, id TMDB) -> liste de titres
        self.kw = keywords or {}          # (media, id TMDB) -> [noms de mots-clés]
        self.discover = discover or {}    # (media, id du mot-clé) -> liste de titres
        self.trending_titles = trending_titles or {}  # media -> liste de titres
        self.external = external or {}    # id TMDB série -> {"tvdb_id": ..., "imdb_id": ...}
        self.find = find or {}            # id TVDB -> id TMDB
        self.fail_ids = set(fail_ids)     # ids dont les recommandations échouent (ApiError)
        self.reco_calls: list[tuple] = []

    def recommendations(self, media, tmdb_id):
        self.reco_calls.append((media, tmdb_id))
        if tmdb_id in self.fail_ids:
            raise ApiError("TMDB indisponible (simulé)")
        return self.recos.get((media, tmdb_id), [])

    def genres(self, media):
        return {18: "Drame", 35: "Comédie", 27: "Horreur", 16: "Animation"}

    def keywords(self, media, tmdb_id):
        return [{"id": KEYWORD_IDS[name], "name": name}
                for name in self.kw.get((media, tmdb_id), [])]

    def discover_by_keyword(self, media, keyword_id, min_votes=100):
        return self.discover.get((media, keyword_id), [])

    def trending(self, media):
        return self.trending_titles.get(media, [])

    def tv_external_ids(self, tmdb_id):
        return self.external.get(tmdb_id, {})

    def find_tv_by_tvdb(self, tvdb_id):
        return self.find.get(tvdb_id)


class ImdbTVMaze(FakeTVMaze):
    """Faux TVMaze qui connaît aussi un id IMDb (-> série 202)."""

    def lookup_imdb(self, imdb_id):
        self._check("lookup_imdb", imdb_id)
        return self.get_show(202) if imdb_id == "tt0202" else None


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


# --- Poids des sources ----------------------------------------------------------------

def test_source_weight():
    assert suggestions.source_weight(COMPLETED, False, None) == 1
    assert suggestions.source_weight(WATCHING, False, None) == 1
    assert suggestions.source_weight(TO_WATCH, False, None) == 1    # l'envie du moment
    assert suggestions.source_weight(FOR_LATER, False, None) == 0.7
    assert suggestions.source_weight(STOPPED, False, None) == -1
    # la note remplace le statut
    assert suggestions.source_weight(STOPPED, False, 5) == 3
    assert suggestions.source_weight(COMPLETED, False, 1) == -2
    # demi-note : entre les deux notes voisines
    assert suggestions.source_weight(COMPLETED, False, 4.5) == 2.5
    assert suggestions.source_weight(COMPLETED, False, 2.5) == 0
    assert suggestions.source_weight(COMPLETED, False, 1.5) == -1.5
    assert suggestions.source_weight(COMPLETED, False, 4.0) == 2
    # favori : bonus, et un favori n'est jamais négatif
    assert suggestions.source_weight(COMPLETED, True, None) == 3
    assert suggestions.source_weight(STOPPED, True, None) == 2


def test_library_sources_order_and_limit(session, monkeypatch):
    monkeypatch.setattr(suggestions, "MAX_SOURCES", 2)
    add_movie(session, "Normal", status=COMPLETED, tmdb_id=1)
    add_movie(session, "Favori", status=COMPLETED, favorite=True, tmdb_id=2)
    add_movie(session, "Arrêté", status=STOPPED, tmdb_id=3)
    add_movie(session, "Sans id", status=COMPLETED, favorite=True)
    library.set_rating(session, MOVIE, 3, 1)  # Arrêté, très mal noté : -2
    session.flush()

    sources = suggestions.library_sources(session, MOVIE)

    # Favori vu, date inconnue : 3 x 0,8 ; la note remplace le statut « arrêté »
    assert [(s.title, s.weight) for s in sources] == [("Favori", 2.4), ("Arrêté", -2)]


# --- Calcul -----------------------------------------------------------------------------

def test_compute_scores_excludes_and_explains(session):
    add_movie(session, "Fight Club", status=COMPLETED, favorite=True, tmdb_id=550)
    add_movie(session, "Seven", status=COMPLETED, tmdb_id=807)
    add_movie(session, "Déjà là", status=TO_WATCH, tmdb_id=900)
    session.add(DismissedSuggestion(kind=MOVIE, tmdb_id=901, title="Écarté"))
    session.flush()
    tmdb = RecoTMDB(recos={
        ("movie", 550): [rec(10, "Memento"), rec(900, "Déjà là"), rec(901, "Écarté"),
                         rec(11, "Confidentiel", votes=3)],
        ("movie", 807): [rec(12, "Zodiac", genres=(27,)), rec(10, "Memento")],
        ("movie", 900): [rec(12, "Zodiac", genres=(27,))],
    })

    result = suggestions.compute(session, tmdb, MOVIE)

    assert [s.title for s in result] == ["Memento", "Zodiac"]
    memento, zodiac = result
    assert memento.because == ["Fight Club", "Seven"]   # le favori compte plus
    assert zodiac.because == ["Déjà là", "Seven"]   # « à voir » : 1 ; vu, date inconnue : 0,8
    assert zodiac.genres == ["Horreur"]
    assert memento.year == 2019
    assert memento.poster_url.endswith("/p10.jpg")
    assert memento.score > zodiac.score


def test_compute_negative_source_pushes_away(session):
    add_show(session, "Aimée", status=COMPLETED, tmdb_id=1)
    add_show(session, "Arrêtée", status=STOPPED, tmdb_id=2)
    session.flush()
    tmdb = RecoTMDB(recos={
        ("tv", 1): [rec(20, "Proche des deux", tv=True), rec(21, "Bonne", tv=True)],
        ("tv", 2): [rec(20, "Proche des deux", tv=True), rec(22, "Mauvaise", tv=True)],
    })

    result = suggestions.compute(session, tmdb, SHOW)

    # "Proche des deux" : +1 puis -1 -> 0, retirée ; "Mauvaise" : négative, retirée
    assert [s.title for s in result] == ["Bonne"]
    assert result[0].because == ["Aimée"]


def test_compute_skips_failing_sources(session):
    add_movie(session, "A", status=COMPLETED, tmdb_id=1)
    add_movie(session, "B", status=COMPLETED, tmdb_id=2)
    session.flush()
    tmdb = RecoTMDB(recos={("movie", 2): [rec(30, "Trouvé")]}, fail_ids={1})
    progress = []

    result = suggestions.compute(session, tmdb, MOVIE,
                                 progress=lambda d, t, label: progress.append((d, t, label)))

    assert [s.title for s in result] == ["Trouvé"]
    assert [(d, t) for d, t, label in progress if "Recommandations (films)" in label] == [(1, 2), (2, 2)]


def test_compute_all_sources_failing_raises(session):
    add_movie(session, "A", status=COMPLETED, tmdb_id=1)
    session.flush()
    with pytest.raises(ApiError):
        suggestions.compute(session, RecoTMDB(fail_ids={1}), MOVIE)


def test_compute_empty_library(session):
    assert suggestions.compute(session, RecoTMDB(), MOVIE) == []


def test_resolve_show_tmdb_ids(session):
    known = add_show(session, "Connue", tvdb_id=1001)
    unknown = add_show(session, "Inconnue", tvdb_id=9999)
    session.flush()

    found = suggestions.resolve_show_tmdb_ids(session, RecoTMDB(find={1001: 77}))

    assert found == 1
    assert known.tmdb_id == 77 and unknown.tmdb_id is None


# --- Actions ------------------------------------------------------------------------------

def test_dismiss_and_restore(session):
    suggestions.dismiss(session, MOVIE, 10, "Memento")
    suggestions.dismiss(session, MOVIE, 10, "Memento")   # pas de doublon
    session.flush()
    assert [d.title for d in suggestions.dismissed(session)] == ["Memento"]
    assert 10 in suggestions.excluded_ids(session, MOVIE)
    assert 10 not in suggestions.excluded_ids(session, SHOW)

    assert suggestions.undismiss_all(session) == 1
    session.flush()
    assert suggestions.dismissed(session) == []


def test_add_suggested_show_via_tvdb(session):
    tmdb = RecoTMDB(external={5000: {"tvdb_id": 1001, "imdb_id": None}})

    show = suggestions.add_suggested_show(session, tmdb, FakeTVMaze(), 5000, TO_WATCH,
                                          title="Titre français")

    assert show.tvmaze_id == 101 and show.tmdb_id == 5000
    assert show.title == "Titre français"   # TVMaze n'a pas de titre FR (akas vides)
    assert library.get_library_item(session, SHOW, show.id).status == TO_WATCH


def test_add_suggested_show_via_imdb(session):
    tmdb = RecoTMDB(external={6000: {"tvdb_id": 4242, "imdb_id": "tt0202"}})
    tvmaze = ImdbTVMaze()

    show = suggestions.add_suggested_show(session, tmdb, tvmaze, 6000, WATCHING)

    assert show.tvmaze_id == 202 and show.tmdb_id == 6000
    assert ("lookup_tvdb", 4242) in tvmaze.calls and ("lookup_imdb", "tt0202") in tvmaze.calls


def test_add_suggested_show_not_on_tvmaze(session):
    tmdb = RecoTMDB(external={7000: {"tvdb_id": 4242}})
    with pytest.raises(ValueError, match="introuvable sur TVMaze"):
        suggestions.add_suggested_show(session, tmdb, ImdbTVMaze(), 7000, TO_WATCH)
    assert session.exec(select(Show)).all() == []


def test_add_suggested_movie(session):
    movie = suggestions.add_suggested_movie(session, RecoTMDB(), 550, TO_WATCH)
    assert movie.tmdb_id == 550
    assert library.get_library_item(session, MOVIE, movie.id).status == TO_WATCH


# --- Client TMDB : nouveaux appels ----------------------------------------------------------

def tmdb_server(request):
    path = request.url.path
    if path == "/3/authentication":
        return httpx.Response(200, json={"success": True})
    if path == "/3/tv/42/recommendations":
        return httpx.Response(200, json={"results": [rec(1, "Une", tv=True)]})
    if path == "/3/genre/movie/list":
        return httpx.Response(200, json={"genres": [{"id": 18, "name": "Drame"}]})
    if path == "/3/find/1001":
        return httpx.Response(200, json={"tv_results": [{"id": 77}], "movie_results": []})
    if path == "/3/find/9999":
        return httpx.Response(200, json={"tv_results": []})
    if path == "/3/tv/42/external_ids":
        return httpx.Response(200, json={"tvdb_id": 1001, "imdb_id": "tt1"})
    return httpx.Response(404)


def test_tmdb_client_new_calls(engine):
    api = FakeApi(tmdb_server)
    client = TMDBClient(engine, token="fake-token", api_key="", transport=api.transport,
                        sleep=no_sleep)

    assert [r["name"] for r in client.recommendations("tv", 42)] == ["Une"]
    assert client.recommendations("tv", 42)  # 2e appel : cache
    assert client.recommendations("movie", 1) == []   # 404
    assert client.genres("movie") == {18: "Drame"}
    assert client.find_tv_by_tvdb(1001) == 77
    assert client.find_tv_by_tvdb(9999) is None
    assert client.tv_external_ids(42)["tvdb_id"] == 1001

    assert api.paths.count("/3/tv/42/recommendations") == 1
    assert "/tv/42/recommendations?language=fr-FR" in cache_keys(engine)
    assert all("fake-token" not in key for key in cache_keys(engine))


def test_tmdb_client_unavailable_returns_empty(engine):
    client = TMDBClient(engine, token="", api_key="")
    assert client.recommendations("tv", 42) == []
    assert client.genres("tv") == {}
    assert client.tv_external_ids(42) == {}
    assert client.find_tv_by_tvdb(1001) is None


# --- Score de correspondance, plateformes, filtres ------------------------------------------

def make(kind=MOVIE, tmdb_id=1, year=2020, genres=("Drame",), vote=7.0, match=50, streaming=()):
    return suggestions.Suggestion(
        kind=kind, tmdb_id=tmdb_id, title=f"T{tmdb_id}", original_title=None, year=year,
        overview="", poster_url=None, genres=list(genres), vote_average=vote, match=match,
        score=match / 10, streaming=list(streaming))


def test_match_percent_is_absolute():
    match = suggestions.match_percent
    full = suggestions.MATCH_FULL_COSINE
    assert match(0, 0) == 1                                      # rien en commun : plancher
    assert match(0, full) == round(100 * 0.50 / 0.85)            # 59 : profil seul, au maximum
    assert match(0, 2 * full) == match(0, full)                  # cosinus plafonné
    assert match(suggestions.MATCH_HALF_COLLAB, 0) == round(100 * 0.35 * 0.5 / 0.85)  # 21
    assert match(1e9, full) == 100
    assert match(-3, -0.2) == 1                                  # signaux négatifs : 0
    assert match(5, 0.15) > match(5, 0.10) and match(8, 0.15) > match(5, 0.15)
    assert match(5, 0.15, reduced=True) == round(match(5, 0.15) * suggestions.REDUCED_FACTOR)


def test_compute_match_not_relative_to_best(session):
    add_movie(session, "A", status=COMPLETED, favorite=True, tmdb_id=1)
    add_movie(session, "B", status=COMPLETED, tmdb_id=2)
    session.flush()
    tmdb = RecoTMDB(recos={("movie", 1): [rec(10, "Top"), rec(11, "Moins")],
                           ("movie", 2): [rec(10, "Top")]})

    result = suggestions.compute(session, tmdb, MOVIE)

    assert [s.title for s in result] == ["Top", "Moins"]          # classement inchangé
    for s in result:   # le meilleur n'est plus forcément à 100 % : valeur propre à chaque titre
        assert s.match == suggestions.match_percent(s.collab, s.content)
    assert result[0].match < 100 and result[0].match > result[1].match
    assert result[0].vote_count == 100


def test_filter_items_relevance_follows_score_not_match():
    a, b = make(tmdb_id=1, match=40), make(tmdb_id=2, match=90)
    a.score, b.score = 0.8, 0.5   # a : mieux classé (qualité, diversité...) malgré un % plus bas
    assert [s.tmdb_id for s in suggestions.filter_items([b, a])] == [1, 2]


def test_attach_availability():
    class AvailTMDB(RecoTMDB):
        def watch_providers(self, media, tmdb_id):
            if tmdb_id == 1:
                return {"FR": {"flatrate": [{"provider_name": "Netflix"}],
                               "free": [{"provider_name": "arte"}, {"provider_name": "Netflix"}],
                               "rent": [{"provider_name": "Apple TV"}]}}
            return {}

    items = [make(tmdb_id=1), make(tmdb_id=2)]
    suggestions.attach_availability(AvailTMDB(), items)

    assert items[0].streaming == ["Netflix", "arte"] and items[0].rent_or_buy
    assert items[1].streaming == [] and not items[1].rent_or_buy


def test_filter_items_criteria():
    items = [
        make(SHOW, 1, year=2015, genres=("Drame",), vote=8.0, match=90, streaming=("Netflix",)),
        make(MOVIE, 2, year=2022, genres=("Horreur",), vote=6.0, match=100),
        make(MOVIE, 3, year=None, genres=("Drame", "Horreur"), vote=7.5, match=40,
             streaming=("Canal+",)),
    ]
    ids = lambda **kw: [s.tmdb_id for s in suggestions.filter_items(items, **kw)]

    assert ids() == [2, 1, 3]                                   # pertinence
    assert ids(kinds=(MOVIE,)) == [2, 3]
    assert ids(genres=("Drame",)) == [1, 3]
    assert ids(platforms=("Netflix", "Disney+")) == [1]
    assert ids(streaming_only=True) == [1, 3]
    assert ids(years=(2010, 2020)) == [1]                       # sans année : exclu
    assert ids(min_vote=7.0) == [1, 3]
    assert ids(sort=suggestions.SORT_RATING) == [1, 3, 2]
    assert ids(sort=suggestions.SORT_RECENT) == [2, 1, 3]


# --- Moteur hybride : signaux, thèmes, tendances, diversité ---------------------------------

def test_engagement_and_recency_factors():
    from datetime import date, datetime
    assert suggestions.engagement_factor(SHOW, episodes=1) == pytest.approx(0.64, abs=0.01)
    assert suggestions.engagement_factor(SHOW, episodes=80) == pytest.approx(1.3)
    assert suggestions.engagement_factor(MOVIE, plays=2) == suggestions.REWATCH_FACTOR
    today = date(2026, 10, 1)
    assert suggestions.recency_factor(datetime(2026, 10, 1), today) == pytest.approx(1.3)
    assert suggestions.recency_factor(datetime(2016, 1, 1), today) == pytest.approx(0.8, abs=0.01)
    assert suggestions.recency_factor(None, today) == 0.8


def test_library_signals_use_activity_and_ignore_import_days(session):
    from datetime import date, datetime
    from helpers_db import add_episode
    from watchlog.core.models import EPISODE, Watch
    binged = add_show(session, "Dévorée", status=COMPLETED, tmdb_id=1, genres="Drame")
    tried = add_show(session, "Goûtée", status=COMPLETED, tmdb_id=2)
    for n in range(1, 61):
        episode = add_episode(session, binged, 1, n)
        session.add(Watch(kind=EPISODE, ref_id=episode.id,
                          last_watched_at=datetime(2026, 9, 30, 20)))  # jour d'import (60 vus)
    episode = add_episode(session, tried, 1, 1)
    session.add(Watch(kind=EPISODE, ref_id=episode.id, last_watched_at=datetime(2026, 9, 1, 20)))
    session.flush()

    signals = suggestions.library_signals(session, SHOW, today=date(2026, 10, 1))

    weights = {s.title: s.weight for s in signals}
    assert weights["Dévorée"] == pytest.approx(1.3 * 0.8, abs=0.01)   # date d'import : inconnue
    assert weights["Goûtée"] == pytest.approx(0.64 * 1.26, abs=0.02)  # récente, mais 1 épisode
    assert signals[0].title == "Dévorée" and signals[0].genres == ["Drame"]


def test_compute_explores_profile_themes_and_trending(session):
    add_movie(session, "Hunger Games", status=COMPLETED, tmdb_id=1)
    add_movie(session, "Divergente", status=COMPLETED, tmdb_id=2)
    session.flush()
    tmdb = RecoTMDB(
        recos={("movie", 1): [rec(10, "Recommandé")], ("movie", 2): [rec(10, "Recommandé")]},
        keywords={("movie", 1): ["dystopia", "survival"], ("movie", 2): ["dystopia", "survival"],
                  ("movie", 10): ["magic"], ("movie", 20): ["dystopia", "survival"],
                  ("movie", 30): ["dystopia"], ("movie", 31): ["magic"]},
        discover={("movie", 4565): [rec(20, "Snowpiercer", votes=5000), rec(1, "Déjà vu")],
                  ("movie", 10349): [rec(20, "Snowpiercer", votes=5000)]},
        trending_titles={"movie": [rec(30, "Tendance proche"), rec(31, "Tendance éloignée")]})

    result = suggestions.compute_all(session, tmdb)

    by_title = {s.title: s for s in result.items}
    assert set(by_title) == {"Recommandé", "Snowpiercer", "Tendance proche"}
    snowpiercer = by_title["Snowpiercer"]
    assert snowpiercer.from_themes and snowpiercer.collab == 0
    assert snowpiercer.matched_themes == ["Dystopie", "Survie"]
    assert set(snowpiercer.themes) == {"Dystopie", "Survie"}
    assert by_title["Tendance proche"].trending
    assert result.items[0].title == "Snowpiercer"         # très proche du profil
    assert [label for _f, label, _w in result.profile.top(2)] == ["Dystopie", "Survie"]


def test_compute_reduced_theme_sinks_titles(session):
    add_movie(session, "A", status=COMPLETED, tmdb_id=1)
    session.flush()
    tmdb = RecoTMDB(recos={("movie", 1): [rec(10, "Dessin animé", genres=(16,)),
                                          rec(11, "Film", genres=(18,))]})
    assert [s.title for s in suggestions.compute(session, tmdb, MOVIE)] == ["Dessin animé", "Film"]

    from watchlog.core import taste
    taste.set_preferences(session, more={}, less={"g:Animation": "Animation"})
    session.flush()

    assert [s.title for s in suggestions.compute(session, tmdb, MOVIE)] == ["Film", "Dessin animé"]


def test_dismissed_titles_push_their_themes_away(session):
    for i in (1, 2):
        add_movie(session, f"Aimé {i}", status=COMPLETED, tmdb_id=i)
    suggestions.dismiss(session, MOVIE, 3, "Écarté")
    suggestions.dismiss(session, MOVIE, 4, "Écarté 2")
    session.flush()
    tmdb = RecoTMDB(keywords={("movie", 1): ["superhero"], ("movie", 2): ["superhero"],
                              ("movie", 3): ["magic"], ("movie", 4): ["magic"]},
                    recos={("movie", 1): [rec(10, "X")]})

    profile = suggestions.compute_all(session, tmdb).profile

    assert profile.weights["k:9715"] > 0 and profile.weights["k:2343"] < 0


def test_diversify_avoids_near_duplicates():
    a, b, c = make(tmdb_id=1, match=100), make(tmdb_id=2, match=95), make(tmdb_id=3, match=80)
    a.features = b.features = {"k:1": "x", "k:2": "y"}   # b : copie de a
    c.features = {"k:3": "z"}
    a.score, b.score, c.score = 1.0, 0.95, 0.8   # scores réels : entre 0 et 1

    ranked = suggestions.diversify([a, b, c], limit=3)

    assert [s.tmdb_id for s in ranked] == [1, 3, 2]
    assert ranked[0].score >= ranked[1].score >= ranked[2].score


def test_filter_items_themes():
    items = [make(tmdb_id=1), make(tmdb_id=2)]
    items[0].themes = ["Dystopie", "Survie"]
    assert [s.tmdb_id for s in suggestions.filter_items(items, themes=("Dystopie",))] == [1]


def test_tmdb_client_keywords_discover_trending(engine):
    def server(request):
        path = request.url.path
        if path == "/3/authentication":
            return httpx.Response(200, json={"success": True})
        if path == "/3/movie/1/keywords":
            return httpx.Response(200, json={"id": 1, "keywords": [{"id": 4565, "name": "dystopia"}]})
        if path == "/3/tv/2/keywords":
            return httpx.Response(200, json={"id": 2, "results": [{"id": 9715, "name": "superhero"}]})
        if path == "/3/discover/movie":
            assert request.url.params["with_keywords"] == "4565"
            return httpx.Response(200, json={"results": [rec(5, "Matrix")]})
        if path == "/3/trending/tv/week":
            return httpx.Response(200, json={"results": [rec(6, "Tendance", tv=True)]})
        return httpx.Response(404)

    api = FakeApi(server)
    client = TMDBClient(engine, token="fake-token", api_key="", transport=api.transport,
                        sleep=no_sleep)

    assert client.keywords("movie", 1) == [{"id": 4565, "name": "dystopia"}]
    assert client.keywords("tv", 2) == [{"id": 9715, "name": "superhero"}]
    assert client.keywords("tv", 3) == []
    assert [r["title"] for r in client.discover_by_keyword("movie", 4565)] == ["Matrix"]
    assert [r["name"] for r in client.trending("tv")] == ["Tendance"]
    client.keywords("movie", 1)
    assert api.paths.count("/3/movie/1/keywords") == 1   # cache



def test_compute_all_reports_tmdb_failures(session):
    """Mots-clés non lus : le résultat est signalé incomplet au lieu d'être faux en silence."""
    for i in (1, 2):
        add_movie(session, f"A{i}", status=COMPLETED, tmdb_id=i)
    session.flush()

    class FlakyTMDB(RecoTMDB):
        def keywords(self, media, tmdb_id):
            raise ApiError("TMDB indisponible (simulé)")

    tmdb = FlakyTMDB(recos={("movie", 1): [rec(10, "X")], ("movie", 2): [rec(10, "X")]})

    result = suggestions.compute_all(session, tmdb)

    assert [s.title for s in result.items] == ["X"]          # le calcul va au bout...
    assert any("2 sur 2 titres de ta bibliothèque" in e for e in result.errors)  # ...et le dit


def test_compute_all_without_failures_has_no_errors(session):
    add_movie(session, "A", status=COMPLETED, tmdb_id=1)
    session.flush()
    result = suggestions.compute_all(session, RecoTMDB(recos={("movie", 1): [rec(10, "X")]}))
    assert result.errors == []


def test_attach_availability_counts_outages():
    class DownTMDB(RecoTMDB):
        def watch_providers(self, media, tmdb_id):
            raise ApiError("TMDB indisponible (simulé)")

    items = [make(tmdb_id=1), make(tmdb_id=2)]
    assert suggestions.attach_availability(DownTMDB(), items) == 2
    assert items[0].streaming == []
