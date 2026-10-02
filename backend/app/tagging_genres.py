"""Conservative style-to-genre screening adapted from upstream 19b44de.

Retains upstream's compound matching and ordinary-word exclusions.
"""
from __future__ import annotations

import re

from .contracts import JsonObject

GENRE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "Trance": ("trance", "eurotrance", "euro trance", "goa", "uplifting trance", "progressive trance"),
    "Psytrance": ("psytrance", "psy trance", "psychedelic trance", "full on"),
    "Eurodance": ("eurodance", "euro dance", "euro house", "eurohouse"),
    "Techno": ("techno", "hard techno", "minimal techno", "acid techno"),
    "House": ("house", "progressive house", "electro house", "funky house", "french house", "disco house"),
    "Deep House": ("deep house",),
    "Tech House": ("tech house",),
    "Drum & Bass": ("drum and bass", "drum n bass", "dnb", "d&b", "liquid funk", "neurofunk"),
    "Jungle": ("jungle",),
    "Dubstep": ("dubstep", "brostep"),
    "Breakbeat": ("breakbeat", "breaks", "big beat"),
    "Hardstyle": ("hardstyle", "hard dance", "hardcore techno", "gabber", "happy hardcore"),
    "Synthwave": ("synthwave", "retrowave", "outrun", "darksynth"),
    "Vaporwave": ("vaporwave",),
    "Synth-pop": ("synth pop", "synthpop", "electropop", "electro pop"),
    "Electro": ("electro", "electro funk"),
    "Electronic": ("electronic", "electronica", "edm", "idm"),
    "Ambient": ("ambient", "drone", "dark ambient"),
    "Chillout": ("chillout", "chill out", "chillwave", "downtempo", "lounge"),
    "Trip Hop": ("trip hop", "triphop"),
    "Future Bass": ("future bass",),
    "Trap": ("trap",),
    "Lo-Fi Hip Hop": ("lofi hip hop", "lo fi hip hop", "lofi", "lo fi"),
    "Hip-Hop": ("hip hop", "hiphop", "rap", "boom bap", "drill"),
    "R&B": ("r&b", "rnb", "r and b", "contemporary r&b"),
    "Disco": ("disco", "nu disco", "italo disco", "eurodisco"),
    "Funk": ("funk", "g funk"),
    "Soul": ("soul", "neo soul", "motown"),
    "Garage": ("uk garage", "garage", "2 step"),
    "Dance": ("dance", "dance pop", "club"),
    "Pop": ("pop", "dream pop", "indie pop", "k pop", "j pop", "city pop", "art pop", "power pop"),
    "Indie": ("indie", "indie rock", "indie folk", "bedroom pop"),
    "Rock": ("rock", "hard rock", "classic rock", "soft rock", "garage rock", "progressive rock", "prog rock", "psychedelic rock", "rock and roll", "post rock"),
    "Alternative": ("alternative", "alt rock", "grunge", "shoegaze", "emo", "post punk"),
    "Punk": ("punk", "pop punk", "hardcore punk"),
    "Metal": ("metal", "heavy metal", "thrash metal", "death metal", "black metal", "power metal", "metalcore", "doom metal"),
    "Folk": ("folk", "acoustic folk", "celtic", "singer songwriter"),
    "Country": ("country", "bluegrass", "americana", "western"),
    "Blues": ("blues", "delta blues"),
    "Jazz": ("jazz", "smooth jazz", "swing", "bebop", "acid jazz", "jazz fusion"),
    "Reggae": ("reggae", "dub", "dancehall", "ska", "reggaeton"),
    "Latin": ("latin", "salsa", "bossa nova", "bachata", "cumbia", "tango", "flamenco"),
    "Classical": ("classical", "baroque", "chamber", "piano sonata", "symphony", "opera"),
    "Soundtrack": ("soundtrack", "cinematic", "orchestral", "film score", "epic orchestral", "game music"),
    "Chiptune": ("chiptune", "8 bit", "8bit", "bitpop", "video game music"),
    "Gospel": ("gospel", "worship"),
    "World": ("world music", "afrobeat", "afrobeats", "balkan", "klezmer"),
    "Children's Music": ("lullaby", "nursery rhyme", "children"),
}

# Words that are also everyday English ("a house on a hill", "dance floor", "country road"). They only
# count as a genre when a whole style tag is exactly that word, e.g. the tag "house".
AMBIGUOUS_PHRASES = {
    "house", "club", "dance", "country", "soul", "western", "drone", "breaks", "goa", "swing", "dub",
    "garage", "trap", "jungle", "lounge", "chamber", "opera", "rap", "children", "folk", "worship",
    "electro", "emo", "punk", "metal", "blues", "latin", "world",
}

_GENRE_PATTERNS: list[tuple[int, re.Pattern[str], str, str]] = sorted(
    (
        (len(phrase), re.compile(r"(?<![a-z0-9&])" + re.escape(phrase) + r"(?![a-z0-9&])"), genre, phrase)
        for genre, phrases in GENRE_SYNONYMS.items()
        for phrase in phrases
    ),
    key=lambda item: -item[0],
)


def _normalise(text: str) -> str:
    return " ".join(re.sub(r"[-_/]+", " ", text.lower()).split())


def _genre_in(text: str) -> str | None:
    normalized = _normalise(text)
    for _, pattern, genre, phrase in _GENRE_PATTERNS:
        if phrase in AMBIGUOUS_PHRASES and normalized != phrase:
            continue
        if pattern.search(normalized):
            return genre
    return None


def guess_genre(params: JsonObject, title: str = "") -> str | None:
    """Prefer an explicit genre, then saved style tags; ordinary prose stays unlabelled.

    Compounds are matched before shorter genre words. Non-string model output
    is ignored rather than converted into an invented description.
    """
    explicit = params.get("genre")
    if isinstance(explicit, str) and explicit.strip():
        return _genre_in(explicit.strip()) or explicit.strip()[:40]
    texts = [text for key in ("style", "prompt", "caption", "sample_query")
             if isinstance(text := params.get(key), str)]
    for text in texts:
        for tag in text.split(","):
            if genre := _genre_in(tag):
                return genre
    for text in texts:
        if genre := _genre_in(text):
            return genre
    return _genre_in(title)
