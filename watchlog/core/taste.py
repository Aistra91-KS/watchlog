"""Profil de goûts : les thèmes et genres qui reviennent dans la bibliothèque.

Les genres TMDB sont trop larges (« Science-Fiction » range ensemble Star Wars et Black
Mirror). Les mots-clés TMDB (« dystopia », « superhero », « time travel »...) décrivent les
thèmes : c'est eux qui permettent de repérer un goût pour la dystopie.

Principe (TF-IDF + cosinus, classique en recherche d'information) :
- chaque titre est décrit par des « traits » : ses mots-clés TMDB (« k:4565 ») et ses
  genres (« g:Animation ») ;
- le profil additionne les traits des titres de la bibliothèque, pondérés par la force du
  signal (note, favori, série regardée en entier...). Les titres arrêtés, mal notés ou
  écartés retirent du poids ;
- un trait présent partout (« based on novel or book ») compte peu (IDF), un trait rare
  mais récurrent chez toi (« dystopia ») compte beaucoup ;
- un candidat ressemble au profil si ses traits vont dans le même sens (cosinus, de 0 à 1).

L'utilisateur peut aussi demander « plus de » ou « moins de » un thème (ThemePreference).
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field

from sqlmodel import Session, select

from watchlog.core.dates import utcnow
from watchlog.core.models import ThemePreference

GENRE_FACTOR = 0.3      # un genre pèse bien moins qu'un mot-clé (trop large)
NEGATIVE_FACTOR = 1.5   # un titre rejeté éloigne plus qu'un titre aimé ne rapproche
MIN_SUPPORT = 2         # un mot-clé vu dans un seul titre aimé n'entre pas dans le profil
BOOST = 1.5             # « plus de ça » : poids du thème = plus fort trait du profil x BOOST

# Mots-clés TMDB qui ne disent rien des goûts : techniques, méta, ou « humeurs »
# (amused, hopeful...) posées de façon très inégale selon les titres.
IGNORED_KEYWORDS = {
    "sequel", "prequel", "duringcreditsstinger", "aftercreditsstinger", "woman director",
    "spin off", "remake", "reboot", "live action remake", "miniseries", "3d", "imax",
    "based on novel or book", "3d animation", "computer animation", "cgi-live action hybrid",
    "live action and animation", "new york city", "cliché", "affectation",
    # humeurs
    "amused", "enthusiastic", "excited", "bold", "hopeful", "hilarious", "adoring", "admiring",
    "cheerful", "joyful", "whimsical", "inspirational", "awestruck", "dramatic", "nostalgic",
    "absurd", "playful", "joyous", "suspenseful", "lighthearted", "appreciative", "intense",
    "complex", "comforting", "powerful", "aggressive", "ambiguous", "wonder", "witty",
    "exhilarated", "depressing", "enchant", "sincere", "mysterious", "farcical", "loving",
    "smutty", "grand", "melancholy", "reflective", "intimate", "optimistic", "vibrant",
    "compassionate", "sympathetic", "romantic", "empathetic", "sentimental", "shocking",
    "anxious", "earnest", "wistful", "thoughtful", "celebratory", "brisk", "provocative",
    "tragic", "tense", "frightened", "horrified", "angry", "defiant", "dignified", "cautionary",
    "critical", "disheartening", "embarrassed", "familiar", "hopeless", "incredulous",
    "inquisitive", "introspective", "kind", "mean spirited", "mischievous", "negative",
    "neutral", "pessimistic", "proud", "regretful", "relaxed", "respectful", "sarcastic",
    "skeptical", "straightforward", "sweet", "vindictive", "zealous", "audacious", "candid",
    "bewildered", "callous", "defiant", "derisive", "demeaning", "approving", "assertive",
    "authoritarian", "baffled", "cynical", "dreary", "foreboding", "gloomy", "harsh",
    "humorous", "instructive", "ominous", "urgent", "frantic", "admiration",
}

# Traduction des mots-clés TMDB (fournis en anglais seulement). Inconnu : nom anglais.
KEYWORDS_FR = {
    "dystopia": "Dystopie", "post-apocalyptic future": "Post-apocalyptique",
    "superhero": "Super-héros", "based on comic": "Adapté d'un comics",
    "villain": "Méchant", "marvel cinematic universe (mcu)": "Univers Marvel (MCU)",
    "dc extended universe (dceu)": "Univers DC (DCEU)", "super power": "Super-pouvoirs",
    "superhuman": "Surhumain", "superhero team": "Équipe de super-héros",
    "secret identity": "Identité secrète", "super villain": "Super-vilain",
    "supervillain": "Super-vilain", "masked superhero": "Super-héros masqué",
    "teen superhero": "Ado super-héros", "anti hero": "Antihéros", "hero": "Héros",
    "vigilante": "Justicier", "crime fighter": "Combattant du crime", "mutant": "Mutants",
    "origin story": "Origines du héros", "magic": "Magie", "friendship": "Amitié",
    "cartoon": "Dessin animé", "anthropomorphism": "Animaux humanisés", "musical": "Comédie musicale",
    "based on young adult novel": "Roman young adult", "pixar": "Pixar", "witch": "Sorcière",
    "coming of age": "Passage à l'âge adulte", "alien": "Extraterrestres", "time travel": "Voyage dans le temps",
    "family": "Famille", "survival": "Survie", "revenge": "Vengeance", "scientist": "Scientifique",
    "female protagonist": "Héroïne", "good versus evil": "Bien contre mal", "robot": "Robots",
    "wizard": "Magicien", "island": "Île", "animals": "Animaux", "space": "Espace",
    "loss of loved one": "Deuil", "princess": "Princesse", "dragon": "Dragons", "cars": "Voitures",
    "monster": "Monstres", "dinosaur": "Dinosaures", "car race": "Course automobile",
    "parent child relationship": "Relation parent-enfant", "martial arts": "Arts martiaux",
    "heist": "Braquage", "based on video game": "Adapté d'un jeu vidéo",
    "family relationships": "Relations familiales", "based on toy": "Adapté d'un jouet",
    "supernatural": "Surnaturel", "murder": "Meurtre", "fantasy world": "Monde imaginaire",
    "small town": "Petite ville", "sibling relationship": "Frères et sœurs", "escape": "Évasion",
    "father daughter relationship": "Relation père-fille", "creature": "Créatures",
    "detective": "Détective", "saving the world": "Sauver le monde", "halloween": "Halloween",
    "friends": "Amis", "thriller": "Thriller", "based on children's book": "Livre pour enfants",
    "lion": "Lion", "alien invasion": "Invasion extraterrestre", "investigation": "Enquête",
    "battle": "Bataille", "betrayal": "Trahison", "artificial intelligence (a.i.)": "Intelligence artificielle",
    "based on true story": "Histoire vraie", "orphan": "Orphelin", "ship": "Navire", "forest": "Forêt",
    "giant robot": "Robots géants", "female villain": "Méchante", "kids": "Enfants",
    "talking animal": "Animal qui parle", "ghost": "Fantômes", "street race": "Courses de rue",
    "fairy tale": "Conte de fées", "lgbt": "LGBT", "sports": "Sport", "racing": "Course",
    "best friend": "Meilleur ami", "space travel": "Voyage spatial", "dark comedy": "Comédie noire",
    "father son relationship": "Relation père-fils", "1980s": "Années 1980", "flying": "Vol",
    "castle": "Château", "philosophical": "Philosophique", "secret organization": "Organisation secrète",
    "kingdom": "Royaume", "school": "École", "elves": "Elfes", "transhumanism": "Transhumanisme",
    "pets": "Animaux de compagnie", "giant monster": "Monstres géants", "conspiracy": "Complot",
    "video game": "Jeu vidéo", "crude humor": "Humour potache", "spy": "Espionnage",
    "sex comedy": "Comédie sexy", "winter": "Hiver", "adventure": "Aventure", "zombie": "Zombies",
    "transformation": "Transformation", "buddy cop": "Duo de flics", "teenager": "Adolescents",
    "on the run": "En fuite", "spacecraft": "Vaisseau spatial", "bravery": "Courage",
    "prince": "Prince", "love": "Amour", "based on fairy tale": "Adapté d'un conte",
    "teamwork": "Travail d'équipe", "quest": "Quête", "teenage girl": "Adolescente",
    "kaiju": "Kaijū", "road trip": "Road trip", "alien planet": "Planète extraterrestre",
    "raunchy comedy": "Comédie grivoise", "inventor": "Inventeur", "virus": "Virus",
    "multiverse": "Multivers", "mad scientist": "Savant fou", "christmas": "Noël",
    "alternative reality": "Réalité alternative", "mission": "Mission", "buddy comedy": "Comédie de potes",
    "dog": "Chien", "dying and death": "La mort", "toy comes to life": "Jouets vivants",
    "bullying": "Harcèlement", "brother brother relationship": "Relation entre frères",
    "journey": "Voyage", "school of witchcraft": "École de sorcellerie", "high fantasy": "High fantasy",
    "teen fantasy": "Fantasy ado", "jungle": "Jungle", "organized crime": "Crime organisé",
    "prehistory": "Préhistoire", "pirate": "Pirates", "holiday": "Vacances",
    "sword and sorcery": "Heroic fantasy", "steampunk": "Steampunk", "neo-noir": "Néo-noir",
    "19th century": "XIXe siècle", "gore": "Gore", "demon": "Démons", "kung fu": "Kung-fu",
    "black magic": "Magie noire", "strong woman": "Femme forte", "rivalry": "Rivalité",
    "magical creature": "Créatures magiques", "science fiction": "Science-fiction", "fbi": "FBI",
    "prison": "Prison", "allegory": "Allégorie", "future": "Futur", "bounty hunter": "Chasseur de primes",
    "biography": "Biographie", "parody": "Parodie", "high school": "Lycée", "war": "Guerre",
    "military": "Militaire", "anime": "Anime", "rescue": "Sauvetage", "dark fantasy": "Dark fantasy",
    "dreams": "Rêves", "isolation": "Isolement", "fantasy": "Fantasy", "chosen one": "L'élu",
    "distant future": "Futur lointain", "portal": "Portail", "secret agent": "Agent secret",
    "space adventure": "Aventure spatiale", "corruption": "Corruption", "death game": "Jeu mortel",
    "global threat": "Menace mondiale", "satire": "Satire", "police": "Police",
    "missing person": "Disparition", "totalitarian regime": "Régime totalitaire",
    "totalitarianism": "Totalitarisme", "rebellion": "Rébellion", "resistance": "Résistance",
    "apocalypse": "Apocalypse", "end of the world": "Fin du monde", "pandemic": "Pandémie",
    "cyberpunk": "Cyberpunk", "clone": "Clones", "cloning": "Clonage", "android": "Androïdes",
    "surveillance": "Surveillance", "mind control": "Contrôle mental", "social commentary": "Critique sociale",
    "mystery": "Mystère", "serial killer": "Tueur en série", "psychological thriller": "Thriller psychologique",
    "time loop": "Boucle temporelle", "parallel world": "Monde parallèle", "simulation": "Simulation",
    "virtual reality": "Réalité virtuelle", "game show": "Jeu télévisé", "underground": "Souterrain",
    "bunker": "Bunker", "hunger": "Faim", "class differences": "Inégalités sociales",
    "near future": "Futur proche", "post-apocalyptic": "Post-apocalyptique", "outbreak": "Épidémie",
    "teen drama": "Drame ado", "young adult": "Young adult", "hacker": "Hacker",
    "cyborg": "Cyborgs", "genetic engineering": "Génie génétique", "experiment": "Expérience",
    "secret": "Secrets", "space opera": "Space opera", "galaxy": "Galaxie", "jedi": "Jedi",
    "star wars": "Star Wars", "comic book": "Comics", "spider": "Araignée", "batman": "Batman",
    "spider-man": "Spider-Man", "x-men": "X-Men", "avengers": "Avengers", "gotham city": "Gotham",
}

# Genres combinés des séries TMDB -> genres simples (comme les films)
TV_GENRE_SPLIT = {
    10759: ("Action", "Aventure"),
    10765: ("Science-Fiction", "Fantastique"),
    10768: ("Guerre",),
    10762: ("Familial",),
}
# Genres TVMaze (déjà traduits en base) alignés sur les genres TMDB
GENRE_ALIASES = {"Anime": "Animation", "Jeunesse": "Familial", "Surnaturel": "Fantastique"}

Features = dict[str, str]  # trait -> libellé lisible


def keyword_label(name: str) -> str:
    return KEYWORDS_FR.get(name, name[:1].upper() + name[1:])


def keyword_features(keywords: list[dict]) -> Features:
    """Mots-clés TMDB ([{id, name}]) -> traits, sans les mots-clés sans intérêt."""
    features = {}
    for kw in keywords:
        name = (kw.get("name") or "").strip().lower()
        if not name or "id" not in kw or name in IGNORED_KEYWORDS or ", " in name:
            continue  # « paris, france » : un lieu, pas un goût
        features[f"k:{kw['id']}"] = keyword_label(name)
    return features


def genre_features(names) -> Features:
    features = {}
    for name in names:
        name = GENRE_ALIASES.get(name, name)
        features[f"g:{name}"] = name
    return features


def tmdb_genre_names(genre_ids, genre_names: dict[int, str]) -> list[str]:
    """Ids de genres TMDB -> noms français ; les genres combinés des séries sont séparés."""
    names = []
    for gid in genre_ids or []:
        for name in TV_GENRE_SPLIT.get(gid, (genre_names.get(gid),)):
            if name and name not in names:
                names.append(name)
    return names


def is_genre(feature: str) -> bool:
    return feature.startswith("g:")


def keyword_id(feature: str) -> int | None:
    return int(feature[2:]) if feature.startswith("k:") else None


@dataclass
class Profile:
    weights: dict[str, float] = field(default_factory=dict)  # trait -> poids (TF-IDF)
    labels: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)     # titres aimés ayant ce trait
    idf: dict[str, float] = field(default_factory=dict)
    reduced: set[str] = field(default_factory=set)            # « moins de ça »
    boosted: set[str] = field(default_factory=set)            # « plus de ça »

    @property
    def norm(self) -> float:
        return math.sqrt(sum(w * w for w in self.weights.values())) or 1.0

    def vector(self, features) -> dict[str, float]:
        """Vecteur TF-IDF d'un titre (trait présent = son IDF)."""
        default = max(self.idf.values(), default=1.0)
        return {f: self.idf.get(f, default) * (GENRE_FACTOR if is_genre(f) else 1.0)
                for f in features}

    def similarity(self, features) -> float:
        """Cosinus entre le titre et le profil (0 = rien en commun ; négatif = à l'opposé)."""
        vec = self.vector(features)
        if not vec:
            return 0.0
        dot = sum(self.weights.get(f, 0.0) * v for f, v in vec.items())
        return dot / (self.norm * math.sqrt(sum(v * v for v in vec.values())))

    def explain(self, features, limit: int = 3) -> list[str]:
        """Thèmes (mots-clés) du titre qui le rapprochent le plus du profil."""
        vec = self.vector(features)
        shared = [(self.weights[f] * v, f) for f, v in vec.items()
                  if self.weights.get(f, 0) > 0 and not is_genre(f)]
        shared.sort(key=lambda pair: (-pair[0], self.labels.get(pair[1], pair[1])))
        return [self.labels.get(f, f) for _w, f in shared[:limit]]

    def top(self, limit: int = 15, keywords_only: bool = False) -> list[tuple[str, str, float]]:
        """Traits les plus marquants du profil : (trait, libellé, poids)."""
        items = [(f, self.labels.get(f, f), w) for f, w in self.weights.items()
                 if w > 0 and not (keywords_only and is_genre(f))]
        items.sort(key=lambda t: (-t[2], t[1]))  # à égalité : ordre alphabétique
        return items[:limit]


def compute_idf(corpus: list[Features]) -> dict[str, float]:
    """IDF lissé : log((1 + N) / (1 + nb de titres ayant le trait)) + 1."""
    df: dict[str, int] = defaultdict(int)
    for features in corpus:
        for f in features:
            df[f] += 1
    n = len(corpus)
    return {f: math.log((1 + n) / (1 + count)) + 1 for f, count in df.items()}


def build_profile(signals: list[tuple[float, Features]], corpus: list[Features],
                  preferences: dict[str, tuple[str, int]] | None = None) -> Profile:
    """Profil à partir des titres de la bibliothèque.

    signals : (poids du titre, traits) ; poids négatif = titre rejeté.
    corpus : traits des autres titres connus (candidats), pour l'IDF ; ceux de la
    bibliothèque (signals) y sont ajoutés.
    preferences : {trait: (libellé, +1 plus de ça / -1 moins de ça)}.
    """
    idf = compute_idf(corpus + [f for _w, f in signals])
    positive, negative = defaultdict(float), defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    labels: dict[str, str] = {}
    total = sum(w for w, _f in signals if w > 0) or 1.0
    for weight, features in signals:
        for f, label in features.items():
            labels[f] = label
            if weight > 0:
                positive[f] += weight
                counts[f] += 1
            elif weight < 0:
                negative[f] -= weight
    weights = {}
    for f in set(positive) | set(negative):
        if counts.get(f, 0) < MIN_SUPPORT and not is_genre(f) and not negative.get(f):
            continue
        value = (positive[f] - NEGATIVE_FACTOR * negative[f]) / total
        value *= idf.get(f, 1.0) * (GENRE_FACTOR if is_genre(f) else 1.0)
        if value:
            weights[f] = value
    profile = Profile(weights=weights, labels=labels, counts=dict(counts), idf=idf)

    strongest = max((w for w in weights.values() if w > 0), default=1.0)
    for f, (label, direction) in (preferences or {}).items():
        profile.labels.setdefault(f, label)
        if direction > 0:
            profile.boosted.add(f)
            profile.weights[f] = max(profile.weights.get(f, 0.0), strongest * BOOST)
        elif direction < 0:
            profile.reduced.add(f)
            profile.weights[f] = min(profile.weights.get(f, 0.0), 0.0)
    return profile


# --- Préférences de thèmes (« plus de ça » / « moins de ça ») ---------------------------

def preferences(session: Session) -> dict[str, tuple[str, int]]:
    """{trait: (libellé, +1 ou -1)}."""
    return {p.feature: (p.label, p.weight) for p in session.exec(select(ThemePreference)).all()}


def set_preferences(session: Session, more: dict[str, str], less: dict[str, str]) -> None:
    """Remplace les préférences : more / less = {trait: libellé}. Pas de commit."""
    wanted = {f: (label, 1) for f, label in more.items()}
    wanted.update({f: (label, -1) for f, label in less.items() if f not in more})
    for row in session.exec(select(ThemePreference)).all():
        if row.feature not in wanted:
            session.delete(row)
        elif (row.label, row.weight) != wanted[row.feature]:
            row.label, row.weight = wanted.pop(row.feature)
            row.updated_at = utcnow()
            session.add(row)
        else:
            wanted.pop(row.feature)
    for f, (label, weight) in wanted.items():
        session.add(ThemePreference(feature=f, label=label, weight=weight, updated_at=utcnow()))
