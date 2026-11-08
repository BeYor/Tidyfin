"""
Language profiles define how a language is detected in a release and how its poster badge looks.

They include metadata, file names, subtitle files, and manual Jellyfin tags.
Presets provide a starting point, and all detection rules can be edited in the dashboard.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .badges import BadgeConfig

# Badge kinds in display order. "audio" is the language's general badge: plain
# audio in the language, or any combination of two or more things (voice-over
# + subtitles, dub + voice-over, ...).
KINDS = ("voiceover", "dub", "audio", "subs")


def _words(value) -> list:
    """Normalise a user-supplied word list (list or comma separated string)."""
    if isinstance(value, str):
        value = value.split(",")
    out = []
    for v in value or []:
        w = str(v).strip().lower()
        if w and w not in out:
            out.append(w)
    return out[:40]


def default_badge_texts(native: str, short: str) -> dict:
    return {
        "audio": native.upper(),
        "dub": f"{short.upper()} DUB", "subs": f"{short.upper()} SUBS", "voiceover": f"{short.upper()} VO",
    }


@dataclass
class LanguageProfile:
    code: str                       # stable id, e.g. "pol"
    name: str = ""                  # English name, e.g. "Polish"
    native: str = ""                # name in the language itself, e.g. "Polski"
    short: str = ""                 # two-letter code used in badge texts and tags
    enabled: bool = True
    codes: list = field(default_factory=list)            # matched exactly against stream language metadata
    hints: list = field(default_factory=list)            # ambiguous words in file names / titles -> "uncertain"
    voiceover_words: list = field(default_factory=list)  # single-voice narration over the original audio
    dub_words: list = field(default_factory=list)        # full dub
    subs_words: list = field(default_factory=list)       # subtitles
    tags: dict = field(default_factory=dict)             # optional manual Jellyfin tag overrides
    badges: dict = field(default_factory=dict)           # kind -> BadgeConfig

    # ----- construction -----

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "LanguageProfile":
        d = d or {}
        code = re.sub(r"[^a-z0-9_-]", "", str(d.get("code", "")).lower())[:12] or "custom"
        name = str(d.get("name") or code)[:40]
        native = str(d.get("native") or name)[:40]
        short = (str(d.get("short") or code[:2]).strip()[:3] or code[:2]).lower()
        defaults = default_badge_texts(native, short)
        badges = {}
        raw = d.get("badges") or {}
        for kind in KINDS:
            base = BadgeConfig(text=defaults[kind], corner="bottom-left")
            badges[kind] = BadgeConfig.from_dict(raw.get(kind), base)
        tags = {}
        for k, v in (d.get("tags") or {}).items():
            if k in (*KINDS, "dismiss"):
                tags[k] = _words(v)
        enabled = d.get("enabled", True)
        return cls(code=code, name=name, native=native, short=short,
                   enabled=enabled if isinstance(enabled, bool) else True,
                   codes=_words(d.get("codes")), hints=_words(d.get("hints")),
                   voiceover_words=_words(d.get("voiceover_words")), dub_words=_words(d.get("dub_words")),
                   subs_words=_words(d.get("subs_words")), tags=tags, badges=badges)

    def to_dict(self) -> dict:
        return {
            "code": self.code, "name": self.name, "native": self.native, "short": self.short,
            "enabled": self.enabled, "codes": list(self.codes), "hints": list(self.hints),
            "voiceover_words": list(self.voiceover_words), "dub_words": list(self.dub_words),
            "subs_words": list(self.subs_words), "tags": {k: list(v) for k, v in self.tags.items()},
            "badges": {k: b.to_dict() for k, b in self.badges.items()},
        }

    # ----- derived matching helpers -----

    def tag_map(self) -> dict:
        """Manual Jellyfin tags (lower-case) per kind plus "dismiss"."""
        s = self.short
        generated = {"voiceover": [f"voiceover-{s}"], "dub": [f"dub-{s}"], "audio": [f"audio-{s}"],
                     "subs": [f"subs-{s}"], "dismiss": [f"no-{s}"]}
        return {k: (self.tags.get(k) or generated[k]) for k in generated}

    def hint_words(self) -> list:
        """Whole words that make a file name or stream title look like this language."""
        return sorted(set(self.hints) | set(self.voiceover_words) | set(self.dub_words) | set(self.subs_words))

    def subtitle_file_words(self) -> list:
        """
        Words that identify a subtitle *file* ("movie.de.srt"): the file is
        language-tagged by convention, so short codes are safe here.
        """
        return sorted(set(self.codes) | set(self.hints) | set(self.subs_words))


def word_regex(words) -> Optional["re.Pattern"]:
    words = [w for w in words if w]
    if not words:
        return None
    alt = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    return re.compile(rf"(?<![^\W_])(?:{alt})(?![^\W_])", re.IGNORECASE)


# ---------- presets ----------

def _p(code, name, native, short, codes, hints, voiceover=(), dub=(), subs=(), tags=None, badges=None):
    return {"code": code, "name": name, "native": native, "short": short, "codes": list(codes), "hints": list(hints),
            "voiceover_words": list(voiceover), "dub_words": list(dub), "subs_words": list(subs),
            "tags": tags or {}, "badges": badges or {}}


def _texts(**kw):
    return {k: {"text": v} for k, v in kw.items()}


PRESETS = [
    _p("pol", "Polish", "Polski", "pl", ["pol", "pl", "polish", "polski"], ["pl", "pol", "polski", "polskie", "polish"],
       voiceover=["lektor"], dub=["dubbing", "dub"], subs=["napisy"],
       tags={"voiceover": ["lektor-pl"], "dub": ["dubbing-pl"], "audio": ["audio-pl"], "subs": ["napisy-pl"], "dismiss": ["brak-pl"]},
       badges=_texts(voiceover="LEKTOR PL", dub="DUBBING PL", audio="POLSKI", subs="NAPISY PL")),
    _p("eng", "English", "English", "en", ["eng", "en", "english"], ["eng", "english"], dub=["dubbed"], subs=["engsub", "engsubs"]),
    _p("ger", "German", "Deutsch", "de", ["ger", "deu", "de", "german", "deutsch"], ["ger", "german", "deutsch", "deu"],
       dub=["dubbed", "synchro"], subs=["untertitel"]),
    _p("fre", "French", "Français", "fr", ["fre", "fra", "fr", "french", "français", "francais"], ["french", "francais", "truefrench"],
       dub=["vf", "vff", "vfq", "vfi", "truefrench"], subs=["vostfr", "subfrench"]),
    _p("spa", "Spanish", "Español", "es", ["spa", "es", "spanish", "español", "espanol", "castellano"],
       ["spa", "spanish", "espanol", "castellano", "latino"], dub=["dubbed", "dual"], subs=["subesp", "subtitulado"]),
    _p("ita", "Italian", "Italiano", "it", ["ita", "it", "italian", "italiano"], ["ita", "italian", "italiano"],
       dub=["dubbed", "doppiato"], subs=["subita"]),
    _p("cze", "Czech", "Česky", "cs", ["cze", "ces", "cs", "cz", "czech", "česky", "cesky"], ["cze", "czech", "cesky"],
       dub=["dabing"], subs=["titulky"]),
    _p("rus", "Russian", "Русский", "ru", ["rus", "ru", "russian", "русский"], ["rus", "russian"],
       voiceover=["mvo", "dvo", "avo"], dub=["dub", "dubbed", "дубляж"], subs=["субтитры"]),
    _p("por", "Portuguese", "Português", "pt", ["por", "pt", "portuguese", "português", "portugues"], ["por", "portuguese"],
       dub=["dublado", "dubbed"], subs=["legendado", "legendas"]),
]

# Presets suggested for a browser/UI language (used by the setup wizard).
SUGGESTED_FOR_LOCALE = {"pl": "pol", "en": "eng", "de": "ger", "fr": "fre", "es": "spa", "it": "ita", "cs": "cze", "ru": "rus", "pt": "por"}


def preset_profile(code: str) -> Optional[LanguageProfile]:
    for p in PRESETS:
        if p["code"] == code:
            return LanguageProfile.from_dict(p)
    return None


def presets_public() -> list:
    """Preset list for the dashboard (full profile dicts)."""
    return [LanguageProfile.from_dict(p).to_dict() for p in PRESETS]
