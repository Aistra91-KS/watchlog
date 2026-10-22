# Watchlog


Suivi personnel de séries et de films, en local. Les fiches des séries viennent de
TVMaze, celles des films de TMDB. L'historique peut être importé depuis un export
de l'application Bingers. L'interface s'ouvre dans le navigateur (Streamlit).

## Mise en place (une fois pour toutes)

1. Installer Python 3.11 ou plus récent.
2. Dans un terminal ouvert dans le dossier du projet, créer l'environnement virtuel :
   - Windows : `py -3 -m venv .venv`
   - macOS / Linux : `python3 -m venv .venv`
3. Installer les dépendances avec le Python de cet environnement :
   - Windows : `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`
   - macOS / Linux : `.venv/bin/python -m pip install -r requirements.txt`
4. Copier `.env.example` en `.env`, puis compléter les identifiants TMDB dans `.env`
   (variables `TMDB_READ_TOKEN` et/ou `TMDB_API_KEY`, voir `.env.example`).
   Ils s'obtiennent dans les paramètres API de son compte TMDB.
   Le fichier `.env` reste sur la machine : il ne doit jamais être partagé ni versionné.
   Sans identifiants, l'outil fonctionne quand même, mais les films n'ont pas de métadonnées.
5. Dans VS Code, choisir l'interpréteur du projet :
   Ctrl+Maj+P > « Python: Select Interpreter » > celui du dossier `.venv`.

## Lancer l'outil

- **Sans VS Code** : double-clic sur le raccourci **Watchlog** (Bureau ou menu Démarrer).
  L'outil démarre en arrière-plan (sans fenêtre) et s'ouvre dans le navigateur ; s'il tourne
  déjà, le navigateur s'ouvre directement. Pour l'arrêter : bouton **Arrêter Watchlog** en bas
  du menu de gauche. Journal du serveur : `data/serveur.log`.
- Raccourcis à (re)créer, par exemple si le projet est déplacé :
  `powershell -ExecutionPolicy Bypass -File launcher\install.ps1`. Ils lancent
  `launcher\watchlog_launcher.py` avec le `pythonw.exe` du `.venv` (pas d'exe maison : les
  antivirus bloquent souvent par erreur les exe Python « empaquetés »).
- Après une mise à jour du code, l'interface affiche « Watchlog a été mis à jour » avec un bouton
  **Redémarrer Watchlog** : il relance le serveur et la page se recharge toute seule.
- Dans VS Code : **Ctrl+Maj+B** (tâche « Lancer Watchlog »). Le navigateur s'ouvre
  sur http://localhost:8501.
- Si le port 8501 est déjà pris, c'est en général que l'outil tourne déjà :
  il suffit d'ouvrir http://localhost:8501 dans le navigateur.
- Pour arrêter : fermer le terminal de la tâche (ou Ctrl+C dedans).

Mettre à jour séries et films : c'est automatique à l'ouverture de Watchlog si la dernière mise
à jour date de plus de 24 h. Elle tourne en arrière-plan (« Mise à jour en cours… » dans le
menu, la page se recharge à la fin) ; le menu affiche ensuite « Mis à jour il y a … » et, s'il y
en a eu, les problèmes de la dernière mise à jour. À la demande : bouton « Synchroniser
maintenant » dans la page Paramètres, ou Ctrl+Maj+P > « Tasks: Run Task » > « Synchroniser
Watchlog ». Pas de mise à jour automatique avec `WATCHLOG_OFFLINE=1`.

Durée des épisodes : TVMaze donne pour les séries des chaînes américaines la durée du créneau
de diffusion (60 min, pub comprise, pour un épisode de 42 min). La synchro la remplace par la
durée réelle donnée par TMDB, épisode par épisode, si la date de diffusion concorde (sinon la
durée TVMaze reste, et le rapport le compte). La première synchro lit toutes les saisons
(quelques minutes) ; ensuite, seulement les séries nouvelles ou mises à jour. Une série ajoutée
depuis TVMaze n'a pas d'id TMDB : la synchro le cherche d'abord (via TVDB) ; celles introuvables
sont comptées dans le rapport.

Accès : l'outil n'est joignable que depuis ce PC (`address = "localhost"` dans
`.streamlit/config.toml`). Le serveur n'a pas de mot de passe : ne l'ouvrir au réseau
(`0.0.0.0`) que sur un réseau de confiance.

Mode sombre : menu **⋮** en haut à droite > **Settings** > choisir *Light*, *Dark* ou
*Use system setting* (par défaut, l'outil suit le thème du système). Le choix est
mémorisé par le navigateur.

## Les pages de l'interface

| Page | Rôle |
| --- | --- |
| À regarder | épisodes et films non vus, en cartes avec affiche, triés par date de sortie, ou au choix par dernier visionnage de la série (sections Disponibles / À venir / Sans date, badge « Nouveau », compteurs et temps à rattraper) ; filtres (type, série, disponibilité, statut, prochain épisode seulement, spéciaux, **sur mes plateformes** : titres dispo en abonnement ou gratuitement chez toi ; ceux dont on ne connaît pas les plateformes sont masqués et signalés sous la liste) ; boutons « Vu » et « Vu jusqu'ici » |
| Bibliothèque | toutes les séries et tous les films suivis, avec leur statut et leur progression |
| Calendrier | sorties des séries et films suivis avec leurs affiches, en vue **Mois** (grille de calendrier, épisodes vus en grisé), **Semaine** (grandes affiches) ou **Liste** (sorties non vues des 60 prochains jours, par semaine) ; résumé en chiffres, filtre séries / films, épisodes d'une même série sortis le même jour regroupés (ex. S01E01–E08). Clic sur un titre : sa fiche |
| Suggestions | séries et films à découvrir (voir « Comment marchent les suggestions » plus bas), avec un score « % pour toi », les thèmes en commun, un badge « Tendance de la semaine » et les plateformes. **Ton profil de goûts** : les thèmes détectés (dystopie, super-héros…) et les réglages « Plus de… » / « Moins de… ». Filtres : type, genres, thèmes, plateformes, dispo en streaming, sur mes plateformes, années, note TMDB minimale ; tri par pertinence, note ou date. Clic sur un titre : page **Aperçu** (résumé, casting, bande-annonce, où regarder, titres similaires, bouton Ajouter). Premier calcul : une à deux minutes, ensuite quelques secondes |
| Ajouter | une seule recherche pour les séries (TVMaze) et les films (TMDB), résultats mêlés avec un badge « Série » (bleu) ou « Film » (orange) ; si une des deux API est en panne, l'autre répond quand même. Onglet « Saisie manuelle » pour un titre absent. Série déjà commencée : juste après l'ajout, « Tu as déjà commencé… ? » permet de dire « j'en suis à S2E5 » (tous les épisodes jusque-là marqués vus, hors spéciaux, date inconnue par défaut ou date choisie, statut proposé « En cours »). Un film ajouté en « Terminé » compte comme vu, à la date choisie (« Vu le », facultatif) |
| Statistiques | chiffres clés (temps total, note moyenne…), **temps pour finir ta liste** (épisodes et films sortis, pas vus : au rythme de la semaine, du mois ou de l'année passés, avec la date de fin estimée ; « Plus tard » en option) puis 4 onglets. **Activité** : records (jour, jours d'affilée, mois), calendrier des 12 derniers mois jour par jour, vus et heures par mois, par jour de la semaine, par année — les jours d'import massif (tout l'historique daté du jour de l'import) peuvent être ignorés. **Séries** : temps passé par série, genres, avancement des séries en cours, séries terminées. **Films** : durées, genres, décennies, liste des films vus (recherche, tri). **Notes** : répartition et meilleures notes. Clic sur une ligne d'un tableau : la fiche |
| Paramètres | connexion TMDB, **mes plateformes** (celles auxquelles tu as accès, liste TMDB / JustWatch pour la France ; servent au filtre « Sur mes plateformes »), correspondances à vérifier, import Bingers, synchronisation, export (Excel, CSV, format Bingers), sauvegardes |

En cliquant sur un titre, on ouvre la **fiche** de la série (saisons, épisodes à cocher avec
leur durée, statut, note) ou du film. Chaque fiche indique **où regarder** en France : abonnement (Netflix, Prime
Video, Canal+…), location, achat (données JustWatch via TMDB, mises à jour chaque semaine).
La fiche d'une série montre aussi le **temps passé** à la regarder (revisionnages et
spéciaux compris) et le **temps restant** pour la finir (épisodes diffusés pas vus, hors
spéciaux ; une durée inconnue vaut la moyenne de la série).

**Prochain épisode** : en haut de la fiche d'une série, le prochain épisode non vu (hors
spéciaux) avec son titre, sa date et sa durée, et un bouton « Vu » (puis « Annuler »). S'il
n'est pas encore diffusé : « Prochain épisode : S03E01 le 12/11/2026 ». Rien si tout est vu.

**Date de visionnage** : modifiable dans la fiche du film (champ « Vu le », ou « Date de
visionnage » avant de cliquer « Vu ») et dans la colonne « Vu le » des tableaux d'épisodes.
Pratique pour un titre vu il y a longtemps : il compte alors dans le bon mois des statistiques.
Si tu ne sais plus quand : bouton **Date inconnue** de la fiche du film (ou case « Date inconnue »
avant de cliquer « Vu ») ; le titre reste vu, mais ne compte dans aucun mois.
Pour une série vue il y a longtemps et cochée d'un coup (tout tombe à la date du jour),
le bloc **Changer la date de visionnage de plusieurs épisodes** de la fiche donne une même
date, ou « Date inconnue », à tous les épisodes vus des saisons choisies.

**Revisionnages** : un film déjà vu se marque « Revu » (date du jour par défaut, modifiable) ;
la fiche indique « Vu N fois » et propose « Retirer un revisionnage ». Pour une série, le bloc
**Revisionnage** ajoute (ou retire) un visionnage à tous les épisodes vus des saisons choisies ;
le tableau d'une saison revue montre alors une colonne « Visionnages ». Les statistiques et le
temps passé comptent chaque visionnage.

**Mon avis** (fiche du film) : note, commentaire, émotions et personnage préféré, enregistrés
d'un coup avec « Enregistrer ». Le personnage préféré repris de Bingers y est modifiable.

Statuts possibles : En cours, À voir, Plus tard, Arrêté, Terminé. Une série finie dont tous
les épisodes sont vus passe toute seule en « Terminé ».

### Ajout manuel

Si une série ou un film n'existe pas sur TVMaze / TMDB, la page **Ajouter** permet de le
saisir à la main : titre et, pour une série, le nombre d'épisodes de chaque saison.

### Correspondances à vérifier

Après un import Bingers, certaines séries demandent une vérification (page **Paramètres**,
section « Correspondances à vérifier ») ; leurs visionnages restent « en attente » d'ici là :

- **À confirmer** : la série a été trouvée par son titre seulement. Bouton
  « Confirmer la proposition » si c'est la bonne, sinon rechercher sur TVMaze et « Choisir ».
- **Introuvable** : rechercher la série sur TVMaze et « Choisir » le bon résultat.
- **Numérotation incohérente** : Bingers et TVMaze ne découpent pas les saisons de la même façon
  (ex. Lupin, La casa de papel). Un aperçu montre la correspondance proposée : le n-ième épisode
  vu devient le n-ième épisode TVMaze (hors spéciaux). Vérifier l'aperçu (les titres des épisodes
  TVMaze aident), puis « Appliquer cette correspondance ».

## Ligne de commande

Avec le Python du `.venv` (sous Windows : `.\.venv\Scripts\python.exe`) :

| Commande | Rôle |
| --- | --- |
| `python -m watchlog import chemin\vers\export.zip` | importe un export Bingers |
| `python -m watchlog sync` | met à jour séries (TVMaze) et films (TMDB) |
| `python -m watchlog check` | vérifie la connexion à TMDB |
| `python -m watchlog export` | exporte « À regarder » (Excel), la base complète (CSV) et un .zip pour Bingers |
| `python -m watchlog run` | lance l'interface (comme Ctrl+Maj+B) |

`import` et `sync` acceptent `--no-posters` pour ne pas télécharger les affiches ; elles font
d'abord une sauvegarde de la base. `import`, `sync` et `check` affichent d'abord l'état de TMDB
(« TMDB connecté. » ou la raison du problème).

## Export

- Dans l'interface (page Paramètres, section « Export ») : un bouton pour la liste
  « À regarder » au format Excel (filtres par défaut : séries en cours et à voir, films),
  un pour la base complète (un fichier CSV par table, réunis dans un `.zip`) et un
  **pour Bingers** (voir ci-dessous).
- En ligne de commande : `python -m watchlog export` crée les trois fichiers dans le dossier
  `exports/` (nom avec la date et l'heure). Options : `--excel CHEMIN`, `--csv CHEMIN`,
  `--bingers CHEMIN` pour choisir l'emplacement (avec des options, seuls ces fichiers sont créés).

### Renvoyer ses données dans Bingers

L'export « base complète » est illisible pour Bingers (« Impossible de lire cette archive ») :
Bingers ne lit que des formats connus. Le bouton **Préparer l'export pour Bingers** écrit le
format d'export de Bingers lui-même (`library.csv`, `watches.csv`, `ratings.csv`, `lists.csv`)
et affiche un rapport (titres omis, dates complétées) ; puis **Télécharger pour Bingers (.zip)**.
Dans Bingers : Importer l'historique > Choisir le fichier .zip.

Repris : séries et films, statuts, favoris, épisodes et films vus avec leurs dates et nombre
de visionnages, notes, émotions, personnage préféré, listes. Pas repris : titres saisis à la
main (inconnus de Bingers) et épisodes spéciaux. Un visionnage sans date est daté du jour
d'ajout du titre. Les séries recalées à la main (numérotation différente) sont signalées : leurs
épisodes sont exportés avec la numérotation TVMaze, à vérifier dans Bingers.

Les CSV sont en UTF-8 avec BOM : Excel les ouvre avec les bons accents. Le cache des API
n'est pas exporté (volumineux et inutile).

## Comment marchent les suggestions

1. **Signaux** : chaque titre de la bibliothèque compte plus ou moins. Note et favori d'abord ;
   sinon le statut (« À voir » compte autant qu'un titre vu : c'est l'envie du moment ; arrêté
   = négatif). Une série vue en entier ou un film revu comptent plus, un visionnage récent aussi ;
   l'historique sans vraie date (jour d'import) un peu moins. « Pas intéressé » éloigne.
2. **Profil de goûts** : les mots-clés TMDB (dystopie, super-héros, voyage dans le temps…)
   et genres qui reviennent dans ce que tu aimes. Un thème courant partout pèse peu, un thème
   plus rare mais récurrent chez toi pèse beaucoup.
3. **Candidats** : recommandations TMDB des titres de la bibliothèque (ce que regardent les
   gens qui ont aimé les mêmes titres), titres phares de tes thèmes forts, tendances de la semaine.
4. **Score** : 35 % recommandations, 50 % ressemblance à ton profil, 15 % qualité (note TMDB
   pondérée par le nombre de votes). Puis la liste est diversifiée (pas dix titres presque
   identiques à la suite).

Pour orienter : page Suggestions > **Ton profil de goûts** > « Plus de… » (ex. Dystopie) /
« Moins de… » (ex. Animation), puis « Enregistrer et recalculer ».

## Import Bingers

Dans Bingers, exporter ses données (fichier `.zip`), puis :

```
python -m watchlog import chemin\vers\export.zip
```

On peut aussi importer depuis l'interface : page **Paramètres**, section « Import Bingers ».

L'import peut être relancé sans créer de doublons. À la fin, un rapport indique ce qui a
été importé et ce qui demande une vérification : séries introuvables ou incertaines,
numérotation d'épisodes différente entre Bingers et TVMaze (ces visionnages sont mis
« en attente » jusqu'à confirmation dans l'interface, voir « Correspondances à vérifier »).

Les dates de visionnage reprises de Bingers sont parfois la date de l'import dans Bingers :
beaucoup d'épisodes peuvent donc sembler vus le même jour (visible dans les statistiques).

## Où sont les données

Tout est dans le dossier `data/` (ignoré par git) :

- `data/watchlog.db` : la base SQLite (bibliothèque, visionnages, notes, cache des API) ;
- `data/posters/` : les affiches téléchargées ;
- `data/watchlog.log` : le journal (erreurs d'API, etc.) ;
- `data/backups/` : les sauvegardes.

## Sauvegardes

Une copie de la base est faite automatiquement à chaque lancement de l'interface, avant chaque
import et avant chaque synchronisation (`data/backups/watchlog-AAAAMMJJ-HHMMSS.db`).
Les 5 plus récentes sont gardées. La page Paramètres liste les sauvegardes et propose
« Sauvegarder maintenant ». Si la sauvegarde du lancement échoue, un avertissement reste affiché
en haut de l'interface jusqu'au prochain démarrage.

Pour revenir à une sauvegarde : fermer l'outil, puis remplacer `data/watchlog.db` par la copie
choisie (renommée en `watchlog.db`) et supprimer `data/watchlog.db-wal` et `data/watchlog.db-shm`
s'ils existent.

## Essayer sans toucher à ses données

La variable d'environnement `WATCHLOG_DATA_DIR` indique un autre dossier de données
(base, affiches, journal, sauvegardes). Pratique pour faire des essais sur une copie :

```
$env:WATCHLOG_DATA_DIR = "C:\chemin\vers\essai"     # PowerShell
.\.venv\Scripts\python.exe -m watchlog run
```

Avec en plus `$env:WATCHLOG_OFFLINE = "1"`, l'interface ne teste pas la connexion TMDB
(aucun appel réseau au démarrage ; TMDB apparaît alors « non connecté »).

Fermer le terminal (ou `Remove-Item Env:WATCHLOG_DATA_DIR` et `Remove-Item Env:WATCHLOG_OFFLINE`)
pour revenir aux vraies données.

## Tests

```
python -m pytest
```

Les tests n'appellent jamais les vraies API (faux serveurs).

## Sources et mentions

- Données séries : [TVMaze.com](https://www.tvmaze.com), sous licence
  [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
- Films : This product uses the TMDB API but is not endorsed or certified by TMDB.
  ([themoviedb.org](https://www.themoviedb.org))

Ces mentions sont aussi affichées en bas de l'interface.

## Dépannage

- **« TMDB non connecté : identifiants absents »** : le fichier `.env` n'existe pas ou les
  variables sont vides. Recopier `.env.example` en `.env` et le compléter.
- **« TMDB non connecté : identifiants refusés »** : l'identifiant est faux ou expiré ;
  le recopier depuis son compte TMDB, puis `python -m watchlog check`.
- **« TMDB non connecté : API injoignable »** : pas de réseau ou TMDB en panne ; réessayer plus tard.
- **TVMaze « trop de requêtes (429) »** : rien à faire, l'outil ralentit et réessaie tout
  seul. Si TVMaze reste indisponible, la synchro le signale et les données en cache restent utilisées.
- **Le terminal demande un e-mail au lancement de Streamlit** : normalement évité par
  `.streamlit/config.toml` (`showEmailPrompt = false`), à condition de lancer depuis le
  dossier du projet (c'est le cas de la tâche VS Code et de `python -m watchlog run`).
  Sinon, appuyer simplement sur Entrée.
- **Le détail d'une erreur** est dans `data/watchlog.log`.
