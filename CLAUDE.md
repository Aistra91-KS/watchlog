# Watchlog — guide du projet pour Claude

Outil de suivi de séries et de films, en local, sous Windows : un seul utilisateur par
installation. Il remplace / complète l'app mobile **Bingers** : l'historique vient d'un export
Bingers, et peut y repartir.
Tout le projet (code, commentaires, docstrings, interface, commits) est **en français**.

**Dépôt public** : rien de personnel dans le code, les tests, la doc ni ce fichier (prénom,
email, chemins locaux, goûts, données réelles, clés). Les notes perso du propriétaire (goûts,
constats sur sa vraie bibliothèque) sont dans `CLAUDE.local.md`, non versionné.

Deux apps qui partagent un format d'échange JSON (`core/exchange.py` ↔ `web/js/core/exchange.js`) :
le logiciel Python/Streamlit (local) et une **app web** HTML/JS sans compilation (`web/`,
GitHub Pages, données dans IndexedDB, clé TMDB saisie par l'utilisateur dans Paramètres).

Le [README.md](README.md) est la doc utilisateur (installation, pages, commandes, dépannage) :
le garder à jour à chaque fonctionnalité visible.

## Stack

- Python 3.11+ (le `.venv` tourne en 3.14), **Streamlit** pour l'interface (navigateur,
  http://localhost:8501), **SQLModel / SQLite** (`data/watchlog.db`, mode WAL), **httpx**,
  pandas / openpyxl (exports), pytest.
- APIs : **TVMaze** (séries, sans clé, CC BY-SA) et **TMDB** (films, recommandations,
  mots-clés, plateformes JustWatch ; identifiants dans `.env`, jamais ailleurs).
- Lancement : raccourci Windows (`launcher/`, `pythonw.exe` du `.venv`, pas d'exe maison :
  les antivirus les bloquent), tâche VS Code Ctrl+Maj+B, ou `python -m watchlog run`.

## Commandes utiles

```
.\.venv\Scripts\python.exe -m pytest -q            # tests (aucun appel réseau)
.\.venv\Scripts\python.exe -m watchlog run         # interface
.\.venv\Scripts\python.exe -m watchlog sync|import|check|export
```

Essais sans toucher aux vraies données : copier la base (API de sauvegarde SQLite, pas une
copie de fichier : mode WAL) dans un dossier temporaire et lancer avec
`WATCHLOG_DATA_DIR=<dossier>` (+ `WATCHLOG_OFFLINE=1` pour couper le test TMDB). Sous Git
Bash, préfixer les scripts Python par `PYTHONIOENCODING=utf-8` (console cp1252 sinon).
Ne **jamais** écrire dans `data/` pendant un essai.

## Architecture

```
watchlog/
  __main__.py          ligne de commande (import, sync, check, export, run)
  core/                logique métier, sans Streamlit, testée
    config.py          chemins (DATA_DIR...), .env, logs avec filtre des secrets
    db.py              moteur SQLite, create_all + migration simple (ajout de colonnes)
    models.py          tables SQLModel (voir plus bas)
    http.py            CachedClient : cache SQLite (table apicache), RateLimiter, réessais
    tmdb.py / tvmaze.py clients d'API (méthodes = un endpoint, TTL de cache par endpoint)
    services.py        make_clients()
    importer.py        import d'un export Bingers (.zip), idempotent
    bingers_export.py  export au format Bingers (l'inverse de importer.py)
    catalog.py         écriture des métadonnées API en base, affiches, genres
    adding.py          ajout d'une série / d'un film (recherche ou saisie manuelle)
    library.py         actions : vu, statut, favori, note...
    queue.py           liste « À regarder » + calendrier
    progress.py        vue d'ensemble de la bibliothèque
    stats.py           statistiques (pandas)
    sync.py            mise à jour TVMaze / TMDB (auto au lancement si > 24 h, thread à part)
    settings.py        réglages et états gardés en base (table Setting, valeurs JSON)
    providers.py       plateformes en France (JustWatch via TMDB)
    preview.py         page Aperçu d'un titre TMDB hors bibliothèque
    taste.py           profil de goûts (thèmes TMDB, TF-IDF) + préférences de thèmes
    suggestions.py     moteur de suggestions hybride
    export.py          Excel « À regarder », CSV de la base
    backup.py          sauvegardes (5 gardées)
  ui/
    app.py             point d'entrée Streamlit, navigation, bannière « redémarrer »
    common.py          helpers partagés (clients en cache, affiches, plateformes, chemins des pages)
    views/*.py         une page par fichier (a_regarder, bibliotheque, calendrier, suggestions,
                       apercu, ajouter, statistiques, parametres, serie, film)
launcher/              lanceur Windows + install.ps1 (raccourcis)
web/                   app web (GitHub Pages), modules ES sans compilation ni dépendance
  index.html, css/     page unique (routes #/page), thèmes clair / sombre, aspect Streamlit
  js/core/             logique pure, testée sous Node (port de core/ : library, queue,
                       progress, stats, taste, suggestions, exchange, catalog, adding)
  js/core/taste-data.js GÉNÉRÉ depuis taste.py (tests/test_web_sync.py)
  js/api/              clients TVMaze / TMDB (cache IndexedDB, débit limité, réessais)
  js/storage/          IndexedDB (idb.js), clé TMDB en localStorage (secret.js)
  js/views/            une page par fichier ; gabarit `html` qui échappe tout (dom.js)
  tests/               node --test ; contrat d'échange avec Python (fixtures/)
tests/                 pytest ; conftest.py = faux clients TVMaze/TMDB, faux zip Bingers
```

### Modèle de données (models.py)

- `Show`, `Episode` (TVMaze ; `number=None` = spécial), `Movie` (TMDB). Textes français TMDB
  à part (`Show.summary_fr`, `Episode.title_fr`, lus par la synchro) : afficher
  `show.display_summary` / `episode.display_title` (français, sinon TVMaze).
- `LibraryItem` (kind + ref_id, statut, favori, added_at). Statuts : `watching` (En cours),
  `to_watch` (À voir), `for_later` (Plus tard), `stopped` (Arrêté), `completed` (Terminé).
- `Watch` : une ligne par épisode/film vu (`plays` pour les revisionnages), dates en UTC naïf.
- `Rating` (note 1-5, émotions, personnage préféré), `CustomList(Item)`.
- `PendingWatch` : visionnages Bingers pas encore rattachés (série introuvable, numérotation
  différente) ; `Show.match_status` dit pourquoi (ok, uncertain, not_found, numbering_mismatch,
  remapped).
- `DismissedSuggestion` (« Pas intéressé »), `ThemePreference` (« plus de / moins de » un thème),
  `ApiCache` (réponses d'API ; jamais exporté), `Setting` (clé → valeur JSON : date et rapport
  de la dernière synchro...).
- `kind` + `ref_id` : pas de clé étrangère SQL, c'est le code qui garantit la cohérence.

## Conventions (à respecter)

- **Les fonctions de `core/` ne font pas de commit** : l'appelant (UI, CLI) décide. Le dire
  dans la docstring (« Pas de commit. »).
- Datetimes en base : **UTC naïf** (`dates.utcnow()`), colonnes annotées `NaiveDatetime`.
  Affichage en heure de Paris. Dates seules = `date`.
- Nouvelle colonne : toujours facultative (nullable) → `db.add_missing_columns` l'ajoute
  aux bases existantes. Nouvelle table : `create_all` suffit.
- Appels réseau uniquement via les clients (`tmdb.py`, `tvmaze.py`) → cache + débit limité +
  `ApiError` lisible. Une panne d'API ne doit jamais faire planter une page : message clair.
  Mais un repli ne doit jamais fausser un résultat **en silence** : toute erreur avalée est
  journalisée (`log.warning` avec le titre / l'id) et, si elle change le résultat, comptée et
  montrée (rapport, `Result.errors`, message). `get_json(force=True)` ne se replie pas sur
  l'ancien cache (la synchro doit savoir qu'elle n'a pas eu de donnée fraîche).
  Audit : agent `silent-failure-hunter` (`.claude/agents/`).
- **Secrets** : uniquement dans `.env`, jamais logués, jamais dans les clés de cache (le filtre
  des logs masque aussi les traces d'exception, qu'il garde).
- **Tests** : jamais d'appel réseau. Faux clients de `conftest.py` (`FakeTVMaze`,
  `FakeTMDB`) ou `helpers_http.FakeApi` (httpx.MockTransport) pour tester un client.
  `helpers_db` pour remplir une base de test. Toute nouvelle fonction de `core/` a ses tests.
- Style : docstrings courtes en français qui disent le *pourquoi* ; lignes ≤ 100 caractères ;
  constantes nommées en tête de module avec un commentaire.
- Interface : textes en français, tutoiement. Couleurs des graphiques : épisodes/séries en
  bleu, films en orange (`common.CHART_COLORS`, thèmes clair et sombre).
- Streamlit : une fonction `@st.cache_data` ne peut pas afficher d'élément (barre de
  progression...) ; les callbacks `on_click` et la progression doivent tourner dans le
  thread du script (pas depuis un `ThreadPoolExecutor`).
- Après une modification de `core/`, le serveur doit redémarrer (bannière « Watchlog a été mis
  à jour » + bouton dans `app.py`).
- Commits : messages en français, au format « Zone : ce qui change » (ex. « Suggestions :
  filtres, score « % pour toi » et page Aperçu »).
- **Commit automatique à la fin de chaque travail** (sans attendre qu'on le demande), une fois
  les tests passés : un commit par sujet, sur la branche courante. Pas de push.

### App web (`web/`)
- Port fidèle de `core/` : même règles, mêmes constantes. Un changement de logique côté
  Python se reporte côté JS (et inversement), avec ses tests des deux côtés.
- Format d'échange « watchlog » v1 : changer `exchange.py` ⇒ changer `exchange.js`, puis
  `WATCHLOG_REGEN_FIXTURES=1 pytest tests/test_exchange.py` et
  `node web/tests/tools/web-export-fixture.mjs` (tests de contrat dans les deux sens).
- Sécurité : tout HTML passe par le gabarit `html` de `dom.js` (échappement) ; URL externes via `safeUrl`
  (https seulement) ; un fichier importé n'est pas fiable. La clé TMDB ne va jamais dans
  IndexedDB, le cache, l'export ni la console.
- Tests : `node --test "tests/*.test.js"` dans `web/` ; les vues se testent en rendant leur
  HTML sous Node (`tests/views.test.js`). Essai réel : `python -m http.server` dans `web/`.
- Même règle d'échec non silencieux : `console.warn` + compteur / message visible.
- **Même aspect que l'app Streamlit** : menu à gauche, pleine largeur, mêmes pages et mêmes
  composants. `css/style.css` reprend les mesures de Streamlit (tailles, rayons, bordures,
  couleurs de `.streamlit/config.toml`, pastilles et alertes) ; `views/ui.js` = équivalents
  de `st.metric`, `st.expander`, `st.tabs`, `st.segmented_control`, `st.info`, `help=` ;
  `views/icons.js` = icônes Material de Streamlit en SVG. Police : Source Sans 3 réduite et
  renommée « Watchlog Sans » (`assets/fonts/`, licence OFL). Une page Python qui change
  d'aspect ⇒ la page web aussi (et inversement). Écarts voulus, pour l'accessibilité :
  champs bordés (≥ 3:1), textes et pastilles ≥ 4,5:1, cibles de 44 px au doigt.

## Logique des points sensibles

### Import Bingers (`importer.py`)
Idempotent, ne remplace jamais ce qui est déjà dans Watchlog (Watchlog fait référence).
Séries retrouvées sur TVMaze par id TVDB, sinon par titre (« à confirmer »). Numérotation
Bingers (TVDB) ≠ TVMaze pour certaines séries (Lupin, La casa de papel, Top Gear) : jamais
deviné, visionnages mis en attente et correspondance validée à la main (page Paramètres).
Beaucoup de dates Bingers = date de l'import dans Bingers : les statistiques et les
suggestions détectent ces « jours d'import » (≥ 50 visionnages le même jour) et les ignorent.

### Export Bingers (`bingers_export.py`)
Bingers ne lit que son propre format (ou ceux d'autres trackers) : l'export CSV de la base
lui est illisible. On écrit donc `library.csv`, `watches.csv`, `ratings.csv`, `lists.csv`
(UTF-8 BOM, CRLF, dates `2026-08-13T17:02:42.346Z`). Statuts séries : watching / following
(à voir) / for_later / stopped ; films : following. Test d'aller-retour import → export → import.

### Suggestions (`suggestions.py` + `taste.py`)
Moteur hybride, entièrement local (pas d'IA externe) :
1. **Signaux** : poids de chaque titre = note (5★ = 3 … 1★ = −2) sinon statut (à voir = 1,
   plus tard = 0,7, arrêté = −1), + 2 si favori ; × engagement (série : 0,5 → 1,3 selon le
   nombre d'épisodes vus ; film revu × 1,4) × récence (0,8 → 1,3 ; date inconnue/import = 0,8).
   Titres écartés = −1.
2. **Profil** (`taste.build_profile`) : traits = mots-clés TMDB (`k:<id>`) + genres (`g:<nom>`,
   × 0,3). TF-IDF : fréquence pondérée dans les titres aimés × IDF (bibliothèque + candidats).
   Mot-clé vu dans un seul titre aimé ignoré. Mots-clés « méta » et « humeurs » (sequel,
   aftercreditsstinger, amused...) filtrés (`IGNORED_KEYWORDS`), traduction FR dans
   `KEYWORDS_FR` (à enrichir quand un thème anglais apparaît dans l'interface).
   Préférences : « plus de » = poids du plus fort trait × 1,5 + exploration du thème ;
   « moins de » = score × 0,3.
3. **Candidats** : recommandations TMDB des 200 sources les plus fortes par type (collaboratif),
   `discover` sur les 6 thèmes les plus forts + thèmes « plus de », tendances de la semaine.
   Seuls 120 candidats collaboratifs par type sont analysés (mots-clés).
4. **Score** = 0,35 collaboratif (log, normalisé) + 0,50 cosinus au profil (normalisé)
   + 0,15 qualité (moyenne bayésienne TMDB) + 0,05 si tendance ; puis diversification MMR
   (Jaccard des mots-clés).
5. **« % pour toi »** (`match`, `suggestions.match_percent`) : absolu, pas relatif au meilleur
   titre. Moyenne pondérée 0,50 / 0,35 de min(1, cosinus brut / 0,30) et de
   collab / (collab + 5) (valeurs brutes), × 0,3 si thème « moins de » ; 1 à 100. Sans qualité
   ni tendance ni MMR : l'ordre (`score`) et le % peuvent diverger, le tri « Pertinence » suit
   `score`.
Mots-clés lus en parallèle (6 threads, `RateLimiter` thread-safe), cache 90 jours ; premier
calcul 1-2 min, ensuite quelques secondes. Le résultat est gardé 1 h (`st.cache_resource`,
page Suggestions) ou jusqu'à « Recalculer ».
Pour juger les suggestions sur une vraie bibliothèque : voir `CLAUDE.local.md` (s'il existe).

## Agents (`.claude/agents/`)

- `silent-failure-hunter` : échecs silencieux dans `core/` ou un diff.
- `code-reviewer` : revue de code (qualité, sécurité, fuite de secrets ou de données perso).
- `a11y-architect` : accessibilité (WCAG 2.2) de la page web et de l'interface.
- `seo-specialist` : référencement de la page publique (GitHub Pages).

## Idées et projets futurs

Suggestions :
- **Couche IA (Claude API)** en option : demande en langage naturel (« une série dystopique
  courte, dispo sur Netflix »), résumé du profil en une phrase, explication personnalisée
  d'une suggestion, ré-classement des 30 premiers candidats. Clé dans `.env`, coûts à afficher,
  le moteur local reste la base (fonctionne sans IA).
- Apprendre des actions : titre ajouté depuis les suggestions = signal positif ; « Pas
  intéressé » répété sur un thème = proposer « moins de ce thème ».
- Autres sources « ce que les gens regardent » : listes Trakt / Simkl (API gratuites),
  « Because you watched » TMDB similar, notes IMDb / Rotten Tomatoes.
- Mode « soirée » : durée max, plateforme dispo ce soir, film vs épisode.
- Suggestions de séries à reprendre (en pause depuis longtemps, nouvelle saison sortie).

Autres :
- Notifications (nouvel épisode d'une série en cours, film dispo en streaming).
- Import depuis d'autres trackers (TV Time, Trakt, Letterboxd).
- Notes par épisode dans les statistiques, objectifs annuels.
