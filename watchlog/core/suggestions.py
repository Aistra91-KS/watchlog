"""Suggestions de séries et de films : moteur hybride (collaboratif + contenu + qualité).

1. Signaux : chaque titre de la bibliothèque a un poids. Note, favori et statut donnent la
   base (arrêté ou mal noté = négatif ; « à voir » = l'envie du moment) ; une série
   regardée en entier, un film revu ou un visionnage récent comptent plus, l'historique
   sans date réelle (jour d'import) un peu moins. Les titres écartés (« Pas intéressé ») sont négatifs.
2. Profil de goûts (taste.py) : les mots-clés TMDB (dystopie, super-héros...) et genres qui
   reviennent dans les titres aimés, plus les thèmes demandés (« plus de » / « moins de »).
3. Candidats, de trois origines :
   - collaboratif : recommandations TMDB des titres de la bibliothèque (ce que regardent
     aussi les gens qui ont vu ce titre) ;
   - thèmes : titres les plus votés sur les thèmes forts du profil (TMDB discover) ;
   - tendances : titres les plus regardés de la semaine.
4. Score = 35 % collaboratif + 50 % ressemblance au profil + 15 % qualité (note TMDB
   pondérée par le nombre de votes), petit bonus tendance ; les thèmes « moins de ça »
   font chuter le score. Puis on diversifie (pas dix titres presque identiques à la suite).
5. « % pour toi » (match_percent) : affinité absolue (profil + recommandations), qui ne
   dépend pas des autres candidats ; l'ordre, lui, suit le score.

Les réponses TMDB sont en cache (mots-clés 90 jours, recommandations 30 jours) : seul le
premier calcul est long (une à deux minutes).
"""

import logging
import math
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlmodel import Session, select

from watchlog.core import adding, catalog, providers, stats, taste
from watchlog.core.dates import utcnow
from watchlog.core.http import ApiError
from watchlog.core.models import (COMPLETED, EPISODE, FOR_LATER, MOVIE, SHOW, STOPPED, TO_WATCH,
                                  WATCHING, DismissedSuggestion, Episode, LibraryItem, Movie,
                                  Rating, Show, Watch)
from watchlog.core.tmdb import TMDBClient

log = logging.getLogger(__name__)

# Type de média côté TMDB
TMDB_MEDIA = {SHOW: "tv", MOVIE: "movie"}

# Poids d'une note (1 à 5) : elle remplace le poids du statut.
RATING_WEIGHTS = {5: 3.0, 4: 2.0, 3: 1.0, 2: -1.0, 1: -2.0}
FAVORITE_BONUS = 2.0
# « À voir » : l'envie du moment, aussi parlante qu'un titre vu.
STATUS_WEIGHTS = {STOPPED: -1.0, TO_WATCH: 1.0, FOR_LATER: 0.7}  # autres statuts : 1
DEFAULT_WEIGHT = 1.0
DISMISSED_WEIGHT = -1.0

FULL_ENGAGEMENT = 50    # épisodes vus à partir desquels une série compte à plein
REWATCH_FACTOR = 1.4    # film revu

MAX_SOURCES = 200   # sources par type (séries / films) : limite le nombre d'appels TMDB
MIN_VOTES = {SHOW: 30, MOVIE: 100}  # titres trop confidentiels ou pas encore sortis : ignorés
MAX_REASONS = 3     # titres cités dans « Parce que tu as vu… »
MAX_THEMES = 3      # thèmes cités dans « Thèmes : … »
RERANK_POOL = 120   # candidats collaboratifs gardés (par type) pour l'analyse des thèmes
THEME_SEEDS = 6     # thèmes forts du profil explorés (par type)
WORKERS = 6         # appels TMDB en parallèle (le débit reste limité par RateLimiter)

W_COLLAB, W_CONTENT, W_QUALITY = 0.35, 0.50, 0.15
TRENDING_BONUS = 0.05
REDUCED_FACTOR = 0.3    # titre qui a un thème « moins de ça »
MIN_CONTENT = 0.35      # titre venu des thèmes ou des tendances : ressemblance minimale
DIVERSITY = 0.25        # pénalité de ressemblance avec les titres déjà retenus (MMR)
BAYES_VOTES, BAYES_MEAN = 200, 6.5
# « % pour toi » : valeur absolue (pas relative au meilleur titre), comparable d'un calcul à
# l'autre. Ressemblance : cosinus au profil / MATCH_FULL_COSINE (plafonné à 1 ; sur la vraie
# bibliothèque, les meilleurs titres sont vers 0,3-0,4). Recommandations : collab / (collab +
# MATCH_HALF_COLLAB), soit 50 % pour une recommandation en tête par 3 à 5 titres aimés.
MATCH_FULL_COSINE = 0.30
MATCH_HALF_COLLAB = 5.0
MAX_FAILURE_SHARE = 0.05  # au-delà de 5 % d'appels TMDB ratés, le résultat est signalé incomplet


@dataclass
class Source:
    kind: str
    tmdb_id: int
    title: str
    weight: float
    genres: list[str] = field(default_factory=list)


@dataclass
class Suggestion:
    kind: str                     # show / movie
    tmdb_id: int
    title: str
    original_title: str | None
    year: int | None
    overview: str
    poster_url: str | None
    genres: list[str]
    vote_average: float
    vote_count: int = 0
    score: float = 0.0
    match: int = 0                # « % pour toi », absolu (voir match_percent)
    because: list[str] = field(default_factory=list)  # titres de la bibliothèque à l'origine
    streaming: list[str] = field(default_factory=list)  # plateformes abonnement / gratuites (FR)
    rent_or_buy: bool = False     # disponible en location ou à l'achat (FR)
    themes: list[str] = field(default_factory=list)          # tous ses thèmes (filtre)
    matched_themes: list[str] = field(default_factory=list)  # thèmes en commun avec le profil
    trending: bool = False        # parmi les plus regardés de la semaine
    from_themes: bool = False     # trouvé par l'exploration des thèmes du profil
    collab: float = 0.0           # somme des recommandations pondérées
    content: float = 0.0          # ressemblance au profil (cosinus)
    features: taste.Features = field(default_factory=dict, repr=False)


@dataclass
class Result:
    items: list[Suggestion]
    profile: taste.Profile
    errors: list[str] = field(default_factory=list)   # résultat incomplet : à afficher


class Failures:
    """Appels TMDB ratés pendant un calcul, par étape : le résultat n'est pas faussé en silence."""

    def __init__(self):
        self.counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # étape -> [ratés, total]

    def add(self, step: str, failed: int, total: int) -> None:
        self.counts[step][0] += failed
        self.counts[step][1] += total

    def messages(self) -> list[str]:
        return [f"TMDB n'a pas répondu pour {failed} sur {total} {step} : suggestions incomplètes, "
                "« Recalculer » plus tard."
                for step, (failed, total) in self.counts.items()
                if total and failed / total > MAX_FAILURE_SHARE]


Progress = Callable[[int, int, str], None]


def source_weight(status: str, favorite: bool, rating: int | None) -> float:
    """Poids de base d'un titre de la bibliothèque (note, sinon statut ; bonus favori)."""
    if rating in RATING_WEIGHTS:
        weight = RATING_WEIGHTS[rating]
    else:
        weight = STATUS_WEIGHTS.get(status, DEFAULT_WEIGHT)
    if favorite:
        weight = max(weight, 0) + FAVORITE_BONUS
    return weight


def engagement_factor(kind: str, episodes: int = 0, plays: int = 1) -> float:
    """Série : de 0,5 (un épisode) à 1,3 (50 épisodes et plus). Film revu : 1,4."""
    if kind == SHOW:
        return 0.5 + 0.8 * min(1.0, math.log1p(episodes) / math.log1p(FULL_ENGAGEMENT))
    return REWATCH_FACTOR if plays > 1 else 1.0


def recency_factor(last: datetime | None, today: date | None = None) -> float:
    """Visionnage récent : jusqu'à 1,3 ; il y a un an ~1 ; ancien : 0,8.

    Date inconnue (ou jour d'import) : 0,8, comme un historique ancien.
    """
    if last is None:
        return 0.8
    age = max(0, ((today or date.today()) - last.date()).days)
    return 0.8 + 0.5 * math.exp(-age / 365)


def resolve_show_tmdb_ids(session: Session, tmdb) -> int:
    """Complète l'id TMDB des séries qui n'en ont pas (via leur id TVDB). Pas de commit.

    Cas des séries ajoutées depuis TVMaze, qui ne donne pas l'id TMDB. Renvoie le nombre trouvé.
    """
    found = 0
    for show in session.exec(select(Show).where(Show.tmdb_id.is_(None),
                                                Show.tvdb_id.is_not(None))).all():
        try:
            tmdb_id = tmdb.find_tv_by_tvdb(show.tvdb_id)
        except ApiError as exc:
            log.warning("Id TMDB introuvable pour %s (TVDB %s) : %s", show.title, show.tvdb_id, exc)
            continue
        if tmdb_id:
            show.tmdb_id = tmdb_id
            session.add(show)
            found += 1
    return found


def _bulk_days(session: Session) -> set[date]:
    """Jours d'import (beaucoup de visionnages le même jour) : leurs dates ne disent rien.

    Même détection que les statistiques (stats.count_bulk_days), sur le jour UTC du
    dernier visionnage.
    """
    rows = session.exec(select(Watch.last_watched_at)
                        .where(Watch.last_watched_at.is_not(None))).all()
    return set(stats.count_bulk_days(value.date() for value in rows))


def _activity(session: Session) -> dict[tuple[str, int], tuple[int, int, datetime | None]]:
    """(kind, id) -> (épisodes vus, nombre de visionnages, dernier visionnage daté)."""
    bulk = _bulk_days(session)

    def dated(value):
        return value if value and value.date() not in bulk else None

    activity = {}
    for ref_id, plays, last in session.exec(select(Watch.ref_id, Watch.plays, Watch.last_watched_at)
                                            .where(Watch.kind == MOVIE)):
        activity[(MOVIE, ref_id)] = (0, plays or 1, dated(last))
    episodes: dict[int, list] = defaultdict(lambda: [0, None])
    for show_id, last in session.exec(select(Episode.show_id, Watch.last_watched_at)
                                      .join(Watch, (Watch.kind == EPISODE)
                                            & (Watch.ref_id == Episode.id))):
        entry = episodes[show_id]
        entry[0] += 1
        last = dated(last)
        if last and (entry[1] is None or last > entry[1]):
            entry[1] = last
    for show_id, (count, last) in episodes.items():
        activity[(SHOW, show_id)] = (count, 1, last)
    return activity


def library_signals(session: Session, kind: str | None = None,
                    today: date | None = None) -> list[Source]:
    """Titres de la bibliothèque (avec id TMDB) et la force de leur signal, les plus marquants d'abord."""
    activity = _activity(session)
    result = []
    for k in ((kind,) if kind else (SHOW, MOVIE)):
        table = Show if k == SHOW else Movie
        ratings = {r.ref_id: r.rating for r in session.exec(
            select(Rating).where(Rating.kind == k, Rating.rating.is_not(None))).all()}
        rows = session.exec(select(LibraryItem, table)
                            .join(table, table.id == LibraryItem.ref_id)
                            .where(LibraryItem.kind == k, table.tmdb_id.is_not(None))).all()
        for item, obj in rows:
            weight = source_weight(item.status, item.favorite, ratings.get(obj.id))
            episodes, plays, last = activity.get((k, obj.id), (0, 1, None))
            if weight > 0 and item.status in (WATCHING, COMPLETED):
                weight *= engagement_factor(k, episodes, plays) * recency_factor(last, today)
            result.append((item.added_at, Source(k, obj.tmdb_id, obj.title, round(weight, 3),
                                                 catalog.split_genres(obj.genres))))
    # Les plus marquants (|poids|), puis les plus récemment ajoutés.
    result.sort(key=lambda pair: pair[0] or datetime.min, reverse=True)
    result.sort(key=lambda pair: abs(pair[1].weight), reverse=True)
    return [source for _added, source in result]


def library_sources(session: Session, kind: str) -> list[Source]:
    """Sources des recommandations TMDB pour un type : les MAX_SOURCES plus marquantes."""
    return [s for s in library_signals(session, kind) if s.weight != 0][:MAX_SOURCES]


def dismissed_signals(session: Session) -> list[Source]:
    """Titres écartés : signaux négatifs (leurs thèmes éloignent)."""
    return [Source(d.kind, d.tmdb_id, d.title or "", DISMISSED_WEIGHT)
            for d in session.exec(select(DismissedSuggestion)).all()]


def excluded_ids(session: Session, kind: str) -> set[int]:
    """Ids TMDB à ne jamais suggérer : titres déjà en base et titres écartés."""
    table = Show if kind == SHOW else Movie
    known = set(session.exec(select(table.tmdb_id).where(table.tmdb_id.is_not(None))).all())
    dismissed = set(session.exec(select(DismissedSuggestion.tmdb_id)
                                 .where(DismissedSuggestion.kind == kind)).all())
    return known | dismissed


def _year(value: str | None) -> int | None:
    try:
        return int(value[:4]) if value else None
    except ValueError:
        return None


def _to_suggestion(kind: str, data: dict, genre_names: dict[int, str]) -> Suggestion:
    if kind == SHOW:
        title, original, released = data.get("name"), data.get("original_name"), data.get("first_air_date")
    else:
        title, original, released = data.get("title"), data.get("original_title"), data.get("release_date")
    return Suggestion(
        kind=kind, tmdb_id=data["id"], title=title or original or "Sans titre",
        original_title=original, year=_year(released),
        overview=adding.short_text(data.get("overview"), length=300),
        poster_url=TMDBClient.poster_url(data.get("poster_path")),
        genres=taste.tmdb_genre_names(data.get("genre_ids"), genre_names),
        vote_average=float(data.get("vote_average") or 0),
        vote_count=int(data.get("vote_count") or 0))


# --- Étapes du calcul -------------------------------------------------------------------

def fetch_features(tmdb, refs: list[tuple[str, int]], progress: Progress | None = None,
                   label: str = "", failures: Failures | None = None,
                   step: str = "titres (thèmes)") -> dict[tuple[str, int], taste.Features]:
    """Mots-clés TMDB de chaque titre (kind, id TMDB), lus en parallèle -> traits.

    Un titre dont TMDB ne donne pas les mots-clés a des traits vides ; l'échec est
    journalisé et compté dans `failures`. `progress` est appelé depuis le thread
    principal (exigence de Streamlit).
    """
    refs = list(dict.fromkeys(refs))

    def one(ref):
        try:
            return taste.keyword_features(tmdb.keywords(TMDB_MEDIA[ref[0]], ref[1])), False
        except ApiError as exc:
            log.warning("Mots-clés TMDB non lus pour %s %s : %s", ref[0], ref[1], exc)
            return {}, True

    result, failed = {}, 0
    with ThreadPoolExecutor(WORKERS) as pool:
        for done, (ref, (features, error)) in enumerate(zip(refs, pool.map(one, refs)), start=1):
            result[ref] = features
            failed += error
            if progress and (done % 10 == 0 or done == len(refs)):
                progress(done, len(refs), label)
    if failures is not None:
        failures.add(step, failed, len(refs))
    return result


def collaborative(tmdb, kind: str, sources: list[Source], excluded: set[int],
                  genre_names: dict[int, str], progress: Progress | None = None,
                  label: str = "", failures: Failures | None = None,
                  ) -> tuple[dict[int, Suggestion], dict[int, dict[str, float]]]:
    """Recommandations TMDB de chaque source, additionnées. Renvoie (candidats, raisons).

    Une source dont TMDB ne répond pas est ignorée (journalisé, compté dans `failures`) ;
    ApiError si aucune n'a pu être lue.
    """
    media = TMDB_MEDIA[kind]
    candidates: dict[int, Suggestion] = {}
    reasons: dict[int, dict[str, float]] = defaultdict(dict)  # id -> {titre source: apport}
    failed = 0
    for done, source in enumerate(sources, start=1):
        try:
            results = tmdb.recommendations(media, source.tmdb_id)
        except ApiError as exc:
            log.warning("Recommandations TMDB non lues pour %s : %s", source.title, exc)
            failed += 1
            results = []
        for rank, data in enumerate(results):
            if "id" not in data or data["id"] in excluded:
                continue
            if (data.get("vote_count") or 0) < MIN_VOTES[kind]:
                continue
            # 1 pour le premier recommandé, ~0,5 pour le dernier de la liste
            contribution = source.weight * (1 - rank / (2 * len(results)))
            suggestion = candidates.get(data["id"])
            if suggestion is None:
                suggestion = candidates[data["id"]] = _to_suggestion(kind, data, genre_names)
            suggestion.collab += contribution
            if contribution > 0:
                reasons[data["id"]][source.title] = contribution
        if progress:
            progress(done, len(sources), label)
    if sources and failed == len(sources):
        raise ApiError("TMDB indisponible : aucune recommandation n'a pu être lue.")
    if failures is not None:
        failures.add("titres (recommandations)", failed, len(sources))
    return candidates, reasons


def explore(tmdb, kind: str, profile: taste.Profile, candidates: dict[int, Suggestion],
            excluded: set[int], genre_names: dict[int, str],
            failures: Failures | None = None) -> list[Suggestion]:
    """Ajoute aux candidats les titres phares des thèmes forts du profil et les tendances.

    Renvoie les nouveaux candidats (leurs mots-clés restent à lire).
    """
    media = TMDB_MEDIA[kind]
    seeds = [f for f, _label, _w in profile.top(THEME_SEEDS, keywords_only=True)]
    seeds += [f for f in profile.boosted if taste.keyword_id(f) and f not in seeds]
    new = []

    def add(data: dict, trending: bool = False) -> None:
        if "id" not in data or data["id"] in excluded or (data.get("vote_count") or 0) < MIN_VOTES[kind]:
            return
        suggestion = candidates.get(data["id"])
        if suggestion is None:
            suggestion = candidates[data["id"]] = _to_suggestion(kind, data, genre_names)
            new.append(suggestion)
        if trending:
            suggestion.trending = True
        else:
            suggestion.from_themes = True

    min_votes = 100 if kind == MOVIE else 50
    failed = 0
    for feature in seeds:
        try:
            results = tmdb.discover_by_keyword(media, taste.keyword_id(feature), min_votes)
        except ApiError as exc:
            log.warning("Exploration du thème %s non lue : %s", profile.labels.get(feature), exc)
            failed += 1
            results = []
        for data in results:
            add(data)
    try:
        for data in tmdb.trending(media):
            add(data, trending=True)
    except ApiError as exc:
        log.warning("Tendances TMDB (%s) non lues : %s", media, exc)
        failed += 1
    if failures is not None:
        failures.add("thèmes et tendances explorés", failed, len(seeds) + 1)
    return new


def quality(vote_average: float, vote_count: int) -> float:
    """Note TMDB pondérée par le nombre de votes (moyenne bayésienne), ramenée entre 0 et 1."""
    bayes = (vote_count * vote_average + BAYES_VOTES * BAYES_MEAN) / (vote_count + BAYES_VOTES)
    return min(1.0, max(0.0, (bayes - 5.5) / 3))


def match_percent(collab: float, content: float, reduced: bool = False) -> int:
    """« % pour toi » d'un titre, de 1 à 100, fixe quels que soient les autres candidats.

    Moyenne, avec les poids du score (50 / 35), de la ressemblance au profil (cosinus brut
    ramené sur 0-1 par MATCH_FULL_COSINE) et du signal collaboratif (somme brute des
    recommandations, saturée par MATCH_HALF_COLLAB). La qualité TMDB, le bonus tendance et
    la diversité n'y entrent pas : ils comptent dans l'ordre, pas dans l'affinité. Thème
    « moins de ça » : × REDUCED_FACTOR, comme le score.
    """
    resemblance = min(1.0, max(0.0, content) / MATCH_FULL_COSINE)
    recommended = collab / (collab + MATCH_HALF_COLLAB) if collab > 0 else 0.0
    value = (W_CONTENT * resemblance + W_COLLAB * recommended) / (W_CONTENT + W_COLLAB)
    if reduced:
        value *= REDUCED_FACTOR
    return max(1, min(100, round(100 * value)))


def _keywords(item: Suggestion) -> set[str]:
    return {f for f in item.features if not taste.is_genre(f)}


def diversify(items: list[Suggestion], limit: int) -> list[Suggestion]:
    """Réordonne pour éviter les titres quasi identiques à la suite (MMR).

    Chaque titre perd DIVERSITY x sa ressemblance (Jaccard des mots-clés) avec le titre
    retenu qui lui ressemble le plus. `score` devient ce score ajusté : il décroît dans
    l'ordre renvoyé, un tri par score garde donc la diversification.
    """
    pool = sorted(items, key=lambda s: s.score, reverse=True)
    chosen: list[Suggestion] = []
    while pool and len(chosen) < limit:
        def adjusted(s):
            kws = _keywords(s)
            overlap = max((len(kws & _keywords(c)) / len(kws | _keywords(c))
                           for c in chosen if kws and _keywords(c)), default=0.0)
            return s.score - DIVERSITY * overlap
        best = max(pool[:40], key=adjusted)
        best.score = adjusted(best)
        pool.remove(best)
        chosen.append(best)
    return chosen


def rank(candidates: dict[int, Suggestion], reasons: dict[int, dict[str, float]],
         profile: taste.Profile, limit: int) -> list[Suggestion]:
    """Score final de chaque candidat d'un type, puis tri diversifié."""
    items = list(candidates.values())
    for s in items:
        s.content = profile.similarity({**s.features, **taste.genre_features(s.genres)})
    top_collab = max((s.collab for s in items), default=0.0)
    top_content = max((s.content for s in items), default=0.0)
    kept = []
    for s in items:
        collab = math.log1p(s.collab) / math.log1p(top_collab) if s.collab > 0 and top_collab > 0 else 0.0
        content = s.content / top_content if s.content > 0 and top_content > 0 else 0.0
        if s.collab <= 0 and not ((s.from_themes or s.trending) and content >= MIN_CONTENT):
            continue  # recommandé surtout par des titres rejetés, ou trop loin du profil
        s.score = (W_COLLAB * collab + W_CONTENT * content
                   + W_QUALITY * quality(s.vote_average, s.vote_count)
                   + (TRENDING_BONUS if s.trending else 0.0))
        reduced = bool(profile.reduced & (set(s.features) | set(taste.genre_features(s.genres))))
        if reduced:
            s.score *= REDUCED_FACTOR
        s.match = match_percent(s.collab, s.content, reduced)
        kept.append(s)
    ranked = diversify(kept, limit)
    for s in ranked:
        titles = reasons.get(s.tmdb_id, {})
        s.because = sorted(titles, key=titles.get, reverse=True)[:MAX_REASONS]
        s.matched_themes = profile.explain(s.features, MAX_THEMES)
        s.themes = list(dict.fromkeys(s.features.values()))
    return ranked


def compute_all(session: Session, tmdb, limit: int = 60,
                progress: Progress | None = None) -> Result:
    """Suggestions de séries puis de films, avec le profil de goûts utilisé.

    progress(fait, total, libellé) est appelé à chaque étape. Si TMDB ne répond pour aucun
    titre d'un type, ce type est absent et un message est ajouté à `errors`.
    """
    failures = Failures()
    signals = library_signals(session) + dismissed_signals(session)
    features = fetch_features(tmdb, [(s.kind, s.tmdb_id) for s in signals if s.weight],
                              progress, "1/4 Thèmes de ta bibliothèque", failures,
                              "titres de ta bibliothèque (thèmes)")
    genre_names = {}
    for kind in (SHOW, MOVIE):
        try:
            genre_names[kind] = tmdb.genres(TMDB_MEDIA[kind])
            failures.add("listes de genres", 0, 1)
        except ApiError as exc:
            log.warning("Genres TMDB (%s) non lus : %s", kind, exc)
            genre_names[kind] = {}
            failures.add("listes de genres", 1, 1)

    errors, pools, all_reasons, excluded = [], {}, {}, {}
    for kind, label in ((SHOW, "séries"), (MOVIE, "films")):
        excluded[kind] = excluded_ids(session, kind)
        try:
            candidates, all_reasons[kind] = collaborative(
                tmdb, kind, library_sources(session, kind), excluded[kind], genre_names[kind],
                progress, f"2/4 Recommandations ({label})", failures)
        except ApiError as exc:
            errors.append(f"Suggestions de {label} indisponibles : {exc}")
            continue
        best = sorted(candidates.values(), key=lambda s: s.collab, reverse=True)[:RERANK_POOL]
        pools[kind] = {s.tmdb_id: s for s in best}

    pool_refs = [(k, i) for k, pool in pools.items() for i in pool]
    features.update(fetch_features(tmdb, pool_refs, progress, "3/4 Thèmes des candidats",
                                   failures, "suggestions possibles (thèmes)"))
    corpus = [features.get(ref, {}) for ref in pool_refs]
    signal_features = [(s.weight, {**features.get((s.kind, s.tmdb_id), {}),
                                   **taste.genre_features(s.genres)}) for s in signals]
    profile = taste.build_profile(signal_features, corpus, taste.preferences(session))

    new_refs = []
    for kind, pool in pools.items():
        new_refs += [(kind, s.tmdb_id) for s in explore(tmdb, kind, profile, pool, excluded[kind],
                                                        genre_names[kind], failures)]
    features.update(fetch_features(tmdb, new_refs, progress, "4/4 Exploration de tes thèmes",
                                   failures, "suggestions possibles (thèmes)"))

    items = []
    for kind, pool in pools.items():
        for s in pool.values():
            s.features = features.get((kind, s.tmdb_id), {})
        items += rank(pool, all_reasons[kind], profile, limit)
    return Result(items, profile, errors + failures.messages())


def compute(session: Session, tmdb, kind: str, limit: int = 60,
            progress: Progress | None = None) -> list[Suggestion]:
    """Suggestions d'un seul type (SHOW ou MOVIE). ApiError si TMDB n'a rien pu fournir."""
    result = compute_all(session, tmdb, limit, progress)
    prefix = f"Suggestions de {'séries' if kind == SHOW else 'films'} indisponibles : "
    for error in result.errors:
        if error.startswith(prefix):
            raise ApiError(error.removeprefix(prefix))
    return [s for s in result.items if s.kind == kind]


def attach_availability(tmdb, items: list[Suggestion], progress: Progress | None = None) -> int:
    """Complète chaque suggestion avec ses plateformes en France (pour les filtres).

    Renvoie le nombre de titres dont TMDB n'a pas donné les plateformes (panne) : pour eux,
    « aucune plateforme » ne veut pas dire « pas disponible ».
    """
    failed = 0
    for done, item in enumerate(items, start=1):
        if progress and (done % 10 == 0 or done == len(items)):
            progress(done, len(items), "Plateformes de streaming")
        try:
            data = providers.availability(tmdb, item.kind, item.tmdb_id, raise_errors=True)
        except ApiError:
            failed += 1
            continue
        if data is None:
            continue
        item.streaming = providers.streaming_names(data)
        item.rent_or_buy = bool(data.offers.get("Location") or data.offers.get("Achat"))
    return failed


# --- Actions depuis la page Suggestions --------------------------------------------

def dismiss(session: Session, kind: str, tmdb_id: int, title: str | None = None) -> None:
    """« Pas intéressé » : le titre ne sera plus suggéré. Pas de commit."""
    exists = session.exec(select(DismissedSuggestion).where(
        DismissedSuggestion.kind == kind, DismissedSuggestion.tmdb_id == tmdb_id)).first()
    if exists is None:
        session.add(DismissedSuggestion(kind=kind, tmdb_id=tmdb_id, title=title,
                                        dismissed_at=utcnow()))


def dismissed(session: Session) -> list[DismissedSuggestion]:
    """Titres écartés, les plus récents d'abord."""
    return list(session.exec(select(DismissedSuggestion)
                             .order_by(DismissedSuggestion.dismissed_at.desc())).all())


def undismiss_all(session: Session) -> int:
    """Remet tous les titres écartés dans les suggestions. Pas de commit."""
    rows = dismissed(session)
    for row in rows:
        session.delete(row)
    return len(rows)


def add_suggested_show(session: Session, tmdb, tvmaze, tmdb_id: int, status: str,
                       title: str | None = None) -> Show:
    """Ajoute une série suggérée (id TMDB) : on retrouve la fiche TVMaze via TVDB, sinon IMDb.

    `title` : titre français TMDB, gardé si TVMaze n'en connaît pas.
    """
    ids = tmdb.tv_external_ids(tmdb_id)
    data = None
    if ids.get("tvdb_id"):
        data = tvmaze.lookup_tvdb(ids["tvdb_id"])
    if data is None and ids.get("imdb_id"):
        data = tvmaze.lookup_imdb(ids["imdb_id"])
    if data is None:
        raise ValueError("Série introuvable sur TVMaze : essaie de la chercher dans la page Ajouter.")
    show = adding.add_show(session, tvmaze, data["id"], status)
    if show.tmdb_id is None:
        show.tmdb_id = tmdb_id
    if title and show.title == show.original_title:
        show.title = title
    session.add(show)
    session.flush()
    return show


def add_suggested_movie(session: Session, tmdb, tmdb_id: int, status: str,
                        watched_on: date | None = None) -> Movie:
    return adding.add_movie(session, tmdb, tmdb_id, status, watched_on=watched_on)


# --- Filtres et tri (page Suggestions) ---------------------------------------------

SORT_RELEVANCE, SORT_RATING, SORT_RECENT = "Pertinence", "Note TMDB", "Plus récents"
SORTS = (SORT_RELEVANCE, SORT_RATING, SORT_RECENT)


def filter_items(items: list[Suggestion], kinds: tuple[str, ...] = (SHOW, MOVIE),
                 genres: tuple[str, ...] = (), platforms: tuple[str, ...] = (),
                 streaming_only: bool = False, years: tuple[int, int] | None = None,
                 min_vote: float = 0.0, sort: str = SORT_RELEVANCE,
                 themes: tuple[str, ...] = ()) -> list[Suggestion]:
    """Suggestions filtrées puis triées.

    genres / platforms / themes : au moins un en commun (vide = pas de filtre). years : (min, max)
    inclus ; un titre sans année n'est gardé que si aucun filtre d'années n'est donné.
    """
    result = []
    for item in items:
        if item.kind not in kinds:
            continue
        if genres and not set(genres) & set(item.genres):
            continue
        if themes and not set(themes) & set(item.themes):
            continue
        if platforms and not set(platforms) & set(item.streaming):
            continue
        if streaming_only and not item.streaming:
            continue
        if years and (item.year is None or not years[0] <= item.year <= years[1]):
            continue
        if item.vote_average < min_vote:
            continue
        result.append(item)
    if sort == SORT_RATING:
        result.sort(key=lambda s: (s.vote_average, s.vote_count), reverse=True)
    elif sort == SORT_RECENT:
        result.sort(key=lambda s: (s.year or 0, s.score), reverse=True)
    else:
        # Ordre du moteur (score diversifié) ; le « % pour toi » n'en est qu'une partie.
        result.sort(key=lambda s: s.score, reverse=True)
    return result
