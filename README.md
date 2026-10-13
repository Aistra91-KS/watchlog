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

- Dans VS Code : **Ctrl+Maj+B** (tâche « Lancer Watchlog »). Le navigateur s'ouvre
  sur http://localhost:8501.
- Si le port 8501 est déjà pris, c'est en général que l'outil tourne déjà :
  il suffit d'ouvrir http://localhost:8501 dans le navigateur.
- Pour arrêter : fermer le terminal de la tâche (ou Ctrl+C dedans).

Mettre à jour séries et films : bouton « Synchroniser maintenant » dans la page Paramètres,
ou Ctrl+Maj+P > « Tasks: Run Task » > « Synchroniser Watchlog ».

Mode sombre : menu **⋮** en haut à droite > **Settings** > choisir *Light*, *Dark* ou
*Use system setting* (par défaut, l'outil suit le thème du système). Le choix est
mémorisé par le navigateur.

## Les pages de l'interface

| Page | Rôle |
| --- | --- |
| À regarder | épisodes et films non vus, en une seule liste triée par date de sortie ; filtres (type, série, disponibilité, statut, prochain épisode seulement, spéciaux) ; boutons « Vu » et « Vu jusqu'ici » |
| Bibliothèque | toutes les séries et tous les films suivis, avec leur statut et leur progression |
| Calendrier | sorties non vues des 60 prochains jours, groupées par semaine |
| Suggestions | séries et films à découvrir, d'après les recommandations TMDB des titres de la bibliothèque (favoris et bonnes notes comptent plus, séries arrêtées et mauvaises notes éloignent les titres semblables) ; plateformes de chaque titre ; filtre par genre, « Ajouter » ou « Pas intéressé ». Premier calcul : jusqu'à une minute, ensuite en cache 30 jours |
| Ajouter | ajout d'une série (recherche TVMaze) ou d'un film (recherche TMDB), ou ajout manuel. Un film ajouté en « Terminé » compte comme vu, à la date choisie (« Vu le », facultatif) |
| Statistiques | chiffres clés, épisodes et films vus par mois, temps passé, séries terminées, meilleures notes |
| Paramètres | connexion TMDB, correspondances à vérifier, import Bingers, synchronisation, export, sauvegardes |

En cliquant sur un titre, on ouvre la **fiche** de la série (saisons, épisodes à cocher, statut,
note) ou du film. Chaque fiche indique **où regarder** en France : abonnement (Netflix, Prime
Video, Canal+…), location, achat (données JustWatch via TMDB, mises à jour chaque semaine).

**Date de visionnage** : modifiable dans la fiche du film (champ « Vu le », ou « Date de
visionnage » avant de cliquer « Vu ») et dans la colonne « Vu le » des tableaux d'épisodes.
Pratique pour un titre vu il y a longtemps : il compte alors dans le bon mois des statistiques.
Champ vide : vu, date inconnue.

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
| `python -m watchlog export` | exporte « À regarder » (Excel) et la base complète (CSV) |
| `python -m watchlog run` | lance l'interface (comme Ctrl+Maj+B) |

`import` et `sync` acceptent `--no-posters` pour ne pas télécharger les affiches ; elles font
d'abord une sauvegarde de la base. `import`, `sync` et `check` affichent d'abord l'état de TMDB
(« TMDB connecté. » ou la raison du problème).

## Export

- Dans l'interface (page Paramètres, section « Export ») : un bouton pour la liste
  « À regarder » au format Excel (filtres par défaut : séries en cours et à voir, films),
  un autre pour la base complète (un fichier CSV par table, réunis dans un `.zip`).
- En ligne de commande : `python -m watchlog export` crée les deux fichiers dans le dossier
  `exports/` (nom avec la date et l'heure). Options : `--excel CHEMIN` et/ou `--csv CHEMIN`
  pour choisir l'emplacement (avec une seule option, seul ce fichier est créé).

Les CSV sont en UTF-8 avec BOM : Excel les ouvre avec les bons accents. Le cache des API
n'est pas exporté (volumineux et inutile).

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
« Sauvegarder maintenant ».

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
