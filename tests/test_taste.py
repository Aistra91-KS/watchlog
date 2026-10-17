"""Tests du profil de goûts (mots-clés TMDB, TF-IDF, préférences de thèmes)."""

import pytest
from sqlmodel import Session

from watchlog.core import taste


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


def kw(*pairs):
    return [{"id": i, "name": n} for i, n in pairs]


def test_keyword_features_translates_and_skips_noise():
    features = taste.keyword_features(kw((4565, "dystopia"), (9663, "sequel"), (325765, "amused"),
                                         (212, "london, england"), (1, "kaiju fight"), (2, "")))
    assert features == {"k:4565": "Dystopie", "k:1": "Kaiju fight"}


def test_tmdb_genre_names_splits_tv_genres():
    names = {10765: "Science-Fiction & Fantastique", 18: "Drame"}
    assert taste.tmdb_genre_names([10765, 18, 99], names) == ["Science-Fiction", "Fantastique",
                                                              "Drame"]
    assert taste.genre_features(["Anime", "Drame"]) == {"g:Animation": "Animation",
                                                        "g:Drame": "Drame"}


DYSTOPIA = {"k:1": "Dystopie"}
HERO = {"k:2": "Super-héros"}
COMMON = {"k:3": "Courant"}


def test_profile_rare_recurring_theme_beats_common_one():
    signals = [(1.0, {**DYSTOPIA, **COMMON}), (1.0, {**DYSTOPIA, **COMMON}), (1.0, COMMON)]
    corpus = [COMMON] * 20 + [DYSTOPIA]   # « Courant » est partout ailleurs aussi

    profile = taste.build_profile(signals, corpus)

    assert profile.weights["k:1"] > profile.weights["k:3"] > 0
    assert [label for _f, label, _w in profile.top(2)] == ["Dystopie", "Courant"]
    assert profile.counts["k:1"] == 2


def test_profile_ignores_single_title_keywords_and_rejects_negatives():
    signals = [(1.0, {**HERO, "k:9": "Unique"}), (1.0, HERO), (-1.0, {**HERO, "k:5": "Rejeté"}),
               (-2.0, {"k:5": "Rejeté"})]
    profile = taste.build_profile(signals, [])

    assert "k:9" not in profile.weights          # un seul titre aimé : pas un goût
    assert profile.weights["k:5"] < 0            # vient seulement de titres rejetés
    assert profile.weights["k:2"] > 0            # 2 aimés contre 1 rejeté


def test_similarity_and_explain():
    profile = taste.build_profile([(1.0, {**DYSTOPIA, **HERO}), (1.0, {**DYSTOPIA, **HERO})], [])

    close = profile.similarity({**DYSTOPIA, **HERO})
    partial = profile.similarity({**DYSTOPIA, "k:7": "Autre"})
    assert close == pytest.approx(1.0)
    assert 0 < partial < close
    assert profile.similarity({"k:7": "Autre"}) == 0
    assert profile.similarity({}) == 0
    assert profile.explain({**DYSTOPIA, "g:Drame": "Drame", "k:7": "Autre"}) == ["Dystopie"]


def test_preferences_boost_and_reduce():
    signals = [(1.0, {**HERO, "g:Animation": "Animation"}),
               (1.0, {**HERO, "g:Animation": "Animation"})]
    prefs = {"k:1": ("Dystopie", 1), "g:Animation": ("Animation", -1)}

    profile = taste.build_profile(signals, [], prefs)

    assert profile.boosted == {"k:1"} and profile.reduced == {"g:Animation"}
    assert profile.weights["k:1"] > profile.weights["k:2"]   # demandé : passe devant
    assert profile.weights["g:Animation"] == 0
    assert profile.labels["k:1"] == "Dystopie"


def test_set_preferences_roundtrip(session):
    taste.set_preferences(session, more={"k:1": "Dystopie"}, less={"g:Animation": "Animation"})
    session.flush()
    assert taste.preferences(session) == {"k:1": ("Dystopie", 1), "g:Animation": ("Animation", -1)}

    taste.set_preferences(session, more={"g:Animation": "Animation"}, less={})
    session.flush()
    assert taste.preferences(session) == {"g:Animation": ("Animation", 1)}
