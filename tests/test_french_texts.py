"""Tests des textes français TMDB : titres d'épisodes et résumé des séries."""

from datetime import date

import pytest
from sqlmodel import Session, select

from watchlog.core import catalog, queue
from watchlog.core.dates import utcnow
from watchlog.core.http import ApiError
from watchlog.core.models import ApiCache, Episode, Show
from watchlog.core.sync import sync
from test_runtimes import SeasonTMDB, add_episode, add_show, tmdb_ep


class QuietTVMaze:
    """Faux TVMaze : aucune série modifiée cette semaine."""

    def updated_since(self, since="week"):
        return {}


@pytest.mark.parametrize("name, expected", [
    ("Le bon côté de l'enfer", "Le bon côté de l'enfer"),
    ("  Infecté ", "Infecté"),
    ("Épisode 5", None), ("episode 12", None), ("Episodio 3", None), ("Episode #1.5", None),
    ("Ep. 4", None), ("Folge 2", None), ("", None), (None, None),
    ("Épisode 5 : le retour", "Épisode 5 : le retour"),   # un vrai titre qui commence pareil
])
def test_french_episode_title(name, expected):
    assert catalog.french_episode_title(name) == expected


def test_display_falls_back_to_tvmaze():
    assert Episode(show_id=1, season=1, title="Pilot").display_title == "Pilot"
    assert Episode(show_id=1, season=1, title="Pilot", title_fr="Pilote").display_title == "Pilote"
    assert Show(title="X", summary="English").display_summary == "English"
    assert Show(title="X", summary="English", summary_fr="Français").display_summary == "Français"


def test_apply_tmdb_episodes_takes_french_titles(engine):
    with Session(engine) as s:
        show = add_show(s)
        e1 = add_episode(s, show, 1, 1, date(2020, 1, 1))
        e2 = add_episode(s, show, 1, 2, date(2020, 1, 8))
        e3 = add_episode(s, show, 1, 3, date(2020, 1, 15))
        shifted = add_episode(s, show, 1, 4, date(2020, 1, 22))
        e1.title = "Good News About Hell"
        tmdb = SeasonTMDB({(500, 1): [
            tmdb_ep(1, "2020-01-01", 42, "Le bon côté de l'enfer"),
            tmdb_ep(2, "2020-01-08", None, "Demi-boucle"),     # titre sans durée : pris
            tmdb_ep(3, "2020-01-15", 40, "Épisode 3"),          # générique : pas un titre
            tmdb_ep(4, "2020-03-01", 40, "Autre épisode"),      # autre numérotation : écarté
        ]})

        result = catalog.apply_tmdb_episodes(s, tmdb, show)

        assert (result.titles, result.no_title, result.unmatched) == (2, 1, 1)
        assert (e1.title, e1.title_fr) == ("Good News About Hell", "Le bon côté de l'enfer")
        assert (e2.title_fr, e2.runtime) == ("Demi-boucle", 60)   # durée TVMaze gardée
        assert e3.title_fr is None and e3.runtime == 40
        assert shifted.title_fr is None
        assert show.texts_fr_checked_at is not None

        # Relecture identique : rien de nouveau ; traduction retirée de TMDB : effacée.
        assert catalog.apply_tmdb_episodes(s, tmdb, show).titles == 0
        tmdb.seasons[(500, 1)][1]["name"] = ""
        catalog.apply_tmdb_episodes(s, tmdb, show)
        assert e2.title_fr is None


def test_apply_tmdb_summary(engine):
    with Session(engine) as s:
        show = add_show(s)
        show.summary = "English summary"
        tmdb = SeasonTMDB({}, overviews={500: " Résumé français. "})

        assert catalog.apply_tmdb_summary(s, tmdb, show) is True
        assert (show.summary, show.summary_fr) == ("English summary", "Résumé français.")

        tmdb.overviews[500] = ""                       # pas de traduction française
        assert catalog.apply_tmdb_summary(s, tmdb, show) is False
        assert show.summary_fr is None and show.display_summary == "English summary"

        show.summary_fr = "Gardé"
        del tmdb.overviews[500]                        # série inconnue de TMDB
        assert catalog.apply_tmdb_summary(s, tmdb, show) is False
        assert show.summary_fr == "Gardé"


def test_tvmaze_refresh_keeps_french_title(engine):
    with Session(engine) as s:
        show = add_show(s)
        episode = Episode(show_id=show.id, season=1, number=1, title="Pilot", title_fr="Pilote",
                          tvmaze_episode_id=9001)
        s.add(episode)
        s.flush()
        data = {"id": show.tvmaze_id, "name": show.title, "status": "Ended",
                "summary": "<p>New</p>"}
        show.summary_fr = "Résumé"

        catalog.upsert_show_from_tvmaze(s, data, [{"id": 9001, "season": 1, "number": 1,
                                                   "name": "Pilot (2)"}])

        assert (episode.title, episode.title_fr) == ("Pilot (2)", "Pilote")
        assert (show.summary, show.summary_fr) == ("New", "Résumé")


def test_sync_initial_pass_of_french_texts(engine):
    """Durées déjà lues avant la mise à jour : les textes français sont lus une fois."""
    with Session(engine) as s:
        show = add_show(s, checked_at=utcnow())
        add_episode(s, show, 1, 1, date(2020, 1, 1))
        add_episode(s, show, 1, 2, date(2020, 1, 8))
        s.commit()
    tmdb = SeasonTMDB({(500, 1): [tmdb_ep(1, "2020-01-01", 60, "Pilote"),
                                  tmdb_ep(2, "2020-01-08", 60, "Épisode 2")]},
                      overviews={500: "Résumé français."})

    report = sync(engine, QuietTVMaze(), tmdb, download_posters=False)

    assert (report.titles_fr, report.titles_fr_missing) == (1, 1)
    assert (report.summaries_fr, report.summaries_fr_missing) == (1, 0)
    assert report.errors == []
    lines = report.lines()
    assert any("1 ajoutés ou modifiés, 1 sans traduction" in line for line in lines)
    assert any("Résumés de séries en français (TMDB) : 1 lus" in line for line in lines)
    with Session(engine) as s:
        assert s.exec(select(Show)).one().summary_fr == "Résumé français."
        assert [e.display_title for e in s.exec(select(Episode).order_by(Episode.number))] \
            == ["Pilote", None]   # pas de titre TVMaze dans ce test

    # Deuxième synchro : déjà fait, TMDB n'est pas rappelé.
    tmdb.fetched.clear()
    sync(engine, QuietTVMaze(), tmdb, download_posters=False)
    assert tmdb.fetched == []


def test_sync_french_texts_error_is_retried(engine):
    """Panne de TMDB sur le résumé : rien d'enregistré pour la série, erreur au rapport."""
    with Session(engine) as s:
        show = add_show(s)
        add_episode(s, show, 1, 1, date(2020, 1, 1))
        s.commit()

    class BrokenDetails(SeasonTMDB):
        def details(self, media, tmdb_id):
            raise ApiError("TMDB : réponse inattendue (HTTP 500)")

    tmdb = BrokenDetails({(500, 1): [tmdb_ep(1, "2020-01-01", 41, "Pilote")]})
    report = sync(engine, QuietTVMaze(), tmdb, download_posters=False)

    assert len(report.errors) == 1 and "Série 500" in report.errors[0]
    assert report.titles_fr == 0
    with Session(engine) as s:
        show = s.exec(select(Show)).one()
        assert show.texts_fr_checked_at is None and show.runtimes_checked_at is None
        assert s.exec(select(Episode)).one().title_fr is None


def test_sync_does_not_lock_the_api_cache(engine):
    """Chaque réponse TMDB s'écrit dans le cache par une autre connexion, comme CachedClient :
    la synchro ne doit pas tenir une écriture ouverte entre deux requêtes (« database is
    locked » sur la vraie base)."""
    with Session(engine) as s:
        for tmdb_id in (500, 501):
            show = add_show(s, tmdb_id)
            add_episode(s, show, 1, 1, date(2020, 1, 1))
            add_episode(s, show, 2, 1, date(2021, 1, 1))
        s.commit()

    class CachingTMDB(SeasonTMDB):
        def _cache(self, key):
            with Session(engine) as other:
                other.add(ApiCache(url=key, response_json="{}", fetched_at=utcnow()))
                other.commit()

        def tv_season(self, tmdb_id, season):
            self._cache(f"/tv/{tmdb_id}/season/{season}")
            return super().tv_season(tmdb_id, season)

        def details(self, media, tmdb_id):
            self._cache(f"/tv/{tmdb_id}")
            return super().details(media, tmdb_id)

    seasons = {(i, n): [tmdb_ep(1, f"{2019 + n}-01-01", 40, f"Titre {n}")]
               for i in (500, 501) for n in (1, 2)}
    tmdb = CachingTMDB(seasons, overviews={500: "Résumé", 501: "Résumé"})
    report = sync(engine, QuietTVMaze(), tmdb, download_posters=False)

    assert report.errors == []
    assert (report.titles_fr, report.summaries_fr) == (4, 2)


def test_queue_shows_french_episode_title(engine):
    with Session(engine) as s:
        show = add_show(s)
        episode = add_episode(s, show, 1, 1, date(2020, 1, 1))
        episode.title, episode.title_fr = "Pilot", "Pilote"
        other = add_episode(s, show, 1, 2, date(2020, 1, 8))
        other.title = "Second"
        s.flush()

        items = queue.build_queue(s, today=date(2026, 1, 1))

        assert [item.episode_title for item in items] == ["Pilote", "Second"]
