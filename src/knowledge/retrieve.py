"""Retrieve candidate departments for a grievance from the mandate knowledge base (English, Hindi, Hinglish).

Usage (PowerShell, project root, venv active; run `python -m src.knowledge.build` first):
    python -m src.knowledge.retrieve "gas cylinder delivery nahi hui" --method hybrid --k 3
    python -m src.knowledge.retrieve --check          # the 10 fixed test queries -> docs/retrieval_check.md

Methods
  lexical  TF-IDF over character n-grams (3-5, within words) and words (1-2). Robust to Hinglish spelling
           variation; no model download; deterministic. Needs scikit-learn only.
  dense    multilingual-e5-small sentence embeddings (optional: requirements-embeddings.txt). Embeddings
           of the knowledge base are cached under data/interim/knowledge/ (git-ignored).
  hybrid   reciprocal rank fusion (k=60) of the two department rankings.

Each department is split into chunks: its names, every official AoB item (verbatim) and the project-authored
responsibilities, categories and keyword lists. A department's score is its best chunk score plus half its
second best, so it rises when more than one chunk supports it. Every result keeps its supporting chunks, and
says whether each one is official text or project-authored. A keyword match never proves jurisdiction.

Complaint text is never logged or written to disk by this module; --check writes only the fixed test queries.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from src.config import PROJECT_ROOT

KB_PATH = PROJECT_ROOT / "data" / "processed" / "knowledge" / "mandates.jsonl"
CACHE_DIR = PROJECT_ROOT / "data" / "interim" / "knowledge"
CHECK_MD = PROJECT_ROOT / "docs" / "retrieval_check.md"
DENSE_MODEL = "intfloat/multilingual-e5-small"
RRF_K = 60
SECOND_CHUNK_WEIGHT = 0.5

# Written before the first retrieval run (docs/mandate_knowledge_base.md section 5); not used for tuning.
CHECK_QUERIES = [
    (
        "en",
        "My EPF withdrawal claim has been pending for three months and the PF office is not responding.",
        ["ministry_of_labour_and_employment"],
    ),
    (
        "en",
        "I paid toll twice at the same plaza on the national highway; FASTag deducted money two times.",
        ["ministry_of_road_transport_and_highways"],
    ),
    (
        "en",
        "My income tax refund for last year has still not been credited even though the return was "
        "processed.",
        ["central_board_of_direct_taxes_income_tax"],
    ),
    (
        "en",
        "The post office has not delivered my speed post parcel that I sent two weeks ago.",
        ["department_of_posts"],
    ),
    (
        "hi",
        "मेरे पिताजी की सेना पेंशन पिछले चार महीने से नहीं आई है।",
        ["department_of_ex_servicemen_welfare", "department_of_defence_finance"],
    ),
    (
        "hi",
        "राशन की दुकान वाला हर महीने कम राशन देता है और राशन कार्ड में नाम नहीं जुड़ रहा।",
        ["department_of_food_and_public_distribution"],
    ),
    ("hi", "आधार कार्ड में जन्म तिथि गलत है और अपडेट नहीं हो रहा है।", ["unique_identification_authority_of_india"]),
    (
        "hinglish",
        "gas cylinder book kiya tha 10 din ho gaye abhi tak delivery nahi hui",
        ["ministry_of_petroleum_and_natural_gas"],
    ),
    (
        "hinglish",
        "mera passport police verification ke baad bhi abhi tak nahi aaya",
        ["ministry_of_external_affairs"],
    ),
    (
        "hinglish",
        "nrega me kaam kiya tha lekin 2 mahine se mazdoori nahi mili",
        ["department_of_rural_development"],
    ),
]


@dataclass(frozen=True)
class Chunk:
    ministry_key: str
    kind: str  # name | official | responsibility | category | keywords
    text: str
    citation: str  # e.g. "AoB Department Of Posts item 2 (p38)"; for authored chunks, what they cite or are

    @property
    def official(self) -> bool:
        return self.kind in {"name", "official"}


@dataclass
class Candidate:
    ministry_key: str
    canonical_name: str
    score: float
    evidence: list[tuple[float, Chunk]] = field(default_factory=list)


# Function words in English, Roman-script Hindi and Hindi that carry no department signal. Added in Phase 9
# after the development data showed spurious matches on "nahi", "mila", "kiya" (docs/routing.md); chosen on
# development data only.
STOPWORDS = set(
    """a an the is are was were be been am i my me we our you your it its this that these those to of in on at
    for from by with and or but not no yes have has had do does did done will would can could should please
    sir madam since still yet also there here what when where which who how than then so very too more most
    hai hain tha thi the ho hua hui hue ka ki ke ko se me mein mera meri mere mujhe hum hamara hamari humne
    nahi nahin nhi na bhi abhi tak par pe aur ya kya kaise kab kyun koi kuch sab ab phir fir toh to wala wale
    wali raha rahi rahe gaya gayi gaye kiya kiye karo kar karna karte diya diye liya liye mila mili mile aaya
    aayi aaye bola bole bolta bolte hota hoti ek do din baar bahut
    है हैं था थी थे हो हुआ हुई हुए का की के को से में मेरा मेरी मेरे मुझे हम हमारा हमारी नहीं न भी अभी तक पर और या
    क्या कैसे कब कोई कुछ सब अब फिर तो वाला वाले वाली रहा रही रहे गया गई गए किया दिया लिया मिला मिली आया आई
    एक बार बहुत कृपया""".split()
)


def normalise(text: str, drop_stopwords: bool = True) -> str:
    """Lower-case, keep letters/digits (Latin and Devanagari), drop function words, collapse whitespace."""
    text = re.sub(r"[^\wऀ-ॿ]+", " ", text.lower())
    words = text.split()
    if drop_stopwords:
        words = [w for w in words if w not in STOPWORDS] or words  # never return an empty query
    return " ".join(words)


def load_kb(path: Path = KB_PATH) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"{path} not found: run `python -m src.knowledge.build` first")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_chunks(records: list[dict], official_only: bool = False) -> list[Chunk]:
    """official_only keeps names and AoB items: a diagnostic of how much retrieval rests on authored text."""
    chunks = []
    for r in records:
        k = r["ministry_key"]
        names = [r["canonical_name"], r["official_name"], *r.get("aob_hindi_names", [])]
        chunks.append(
            Chunk(k, "name", " ; ".join(dict.fromkeys(names)), "department names (Part A, AoB heading)")
        )
        for it in r["official_mandate"]:
            cite = f"AoB {it['section'].title()} item {it['item']} (p{it['pdf_page']})"
            chunks.append(Chunk(k, "official", it["text"], cite))
        if official_only:
            continue
        for resp in r["grievance_responsibilities"]:
            chunks.append(Chunk(k, "responsibility", resp["text"], "cites " + ", ".join(resp["aob_items"])))
        for cat in r["common_grievance_categories"]:
            chunks.append(Chunk(k, "category", cat, ""))
        for lang, words in r["keywords"].items():
            chunks.append(Chunk(k, "keywords", " ; ".join(words), f"keywords ({lang})"))
    return chunks


def _aggregate(records: list[dict], chunks: list[Chunk], scores: np.ndarray, k: int) -> list[Candidate]:
    names = {r["ministry_key"]: r["canonical_name"] for r in records}
    by_key: dict[str, list[tuple[float, Chunk]]] = {}
    for s, c in zip(scores, chunks, strict=True):
        by_key.setdefault(c.ministry_key, []).append((float(s), c))
    cands = []
    for key, ev in by_key.items():
        ev.sort(key=lambda x: x[0], reverse=True)
        second = ev[1][0] if len(ev) > 1 else 0.0
        cands.append(Candidate(key, names[key], ev[0][0] + SECOND_CHUNK_WEIGHT * second, ev[:3]))
    cands.sort(key=lambda c: c.score, reverse=True)
    return cands[:k]


class LexicalRetriever:
    def __init__(self, records: list[dict], official_only: bool = False):
        from sklearn.feature_extraction.text import TfidfVectorizer

        self.records = records
        self.chunks = build_chunks(records, official_only)
        docs = [normalise(c.text) for c in self.chunks]
        self.char = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True, min_df=1)
        self.word = TfidfVectorizer(
            analyzer="word", ngram_range=(1, 2), sublinear_tf=True, token_pattern=r"\S+"
        )
        self.char_m = self.char.fit_transform(docs)
        self.word_m = self.word.fit_transform(docs)

    def chunk_scores(self, query: str) -> np.ndarray:
        q = normalise(query)
        c = (self.char_m @ self.char.transform([q]).T).toarray().ravel()
        w = (self.word_m @ self.word.transform([q]).T).toarray().ravel()
        return 0.5 * c + 0.5 * w

    def search(self, query: str, k: int = 3) -> list[Candidate]:
        return _aggregate(self.records, self.chunks, self.chunk_scores(query), k)


class DenseRetriever:
    def __init__(self, records: list[dict], model_name: str = DENSE_MODEL):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # optional dependency
            raise RuntimeError("dense retrieval needs: pip install -r requirements-embeddings.txt") from exc
        self.records = records
        self.chunks = build_chunks(records)
        self.model = SentenceTransformer(model_name, device="cpu")
        texts = ["passage: " + c.text for c in self.chunks]
        digest = hashlib.sha256((model_name + "\n" + "\n".join(texts)).encode("utf-8")).hexdigest()[:16]
        cache = CACHE_DIR / f"kb_embeddings_{digest}.npy"
        if cache.exists():
            self.emb = np.load(cache)
        else:
            self.emb = self.model.encode(
                texts, batch_size=32, normalize_embeddings=True, show_progress_bar=False
            )
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            np.save(cache, self.emb)

    def chunk_scores(self, query: str) -> np.ndarray:
        q = self.model.encode(["query: " + query], normalize_embeddings=True, show_progress_bar=False)[0]
        return self.emb @ q

    def search(self, query: str, k: int = 3) -> list[Candidate]:
        return _aggregate(self.records, self.chunks, self.chunk_scores(query), k)


class HybridRetriever:
    def __init__(self, lexical: LexicalRetriever, dense: DenseRetriever):
        self.lexical, self.dense = lexical, dense

    def search(self, query: str, k: int = 3) -> list[Candidate]:
        n = len(self.lexical.records)
        lex = self.lexical.search(query, n)
        den = {c.ministry_key: (rank, c) for rank, c in enumerate(self.dense.search(query, n), start=1)}
        fused = []
        for rank, c in enumerate(lex, start=1):
            d_rank, d = den[c.ministry_key]
            score = 1 / (RRF_K + rank) + 1 / (RRF_K + d_rank)
            # lexical evidence first, then the best dense chunk if different (the scores are not comparable)
            evidence = c.evidence[:2] + [
                e for e in d.evidence[:1] if e[1] not in {x[1] for x in c.evidence[:2]}
            ]
            fused.append(Candidate(c.ministry_key, c.canonical_name, score, evidence))
        fused.sort(key=lambda c: c.score, reverse=True)
        return fused[:k]


def make_retriever(method: str, records: list[dict] | None = None):
    records = records or load_kb()
    if method == "lexical":
        return LexicalRetriever(records)
    if method == "dense":
        return DenseRetriever(records)
    if method == "hybrid":
        return HybridRetriever(LexicalRetriever(records), DenseRetriever(records))
    raise ValueError(f"unknown method {method!r}")


def run_check(methods: list[str]) -> str:
    records = load_kb()
    lexical = LexicalRetriever(records)
    # diagnostic, not a candidate method: names and official AoB text only, no project-authored keywords
    retrievers = {
        "lexical": lexical,
        "lexical (official text only)": LexicalRetriever(records, official_only=True),
    }
    if {"dense", "hybrid"} & set(methods):
        dense = DenseRetriever(records)
        retrievers.update({"dense": dense, "hybrid": HybridRetriever(lexical, dense)})
    lines = [
        "# Retrieval check: 10 fixed queries (generated)",
        "",
        "Generated by `python -m src.knowledge.retrieve --check`. Do not edit by hand. The queries and their",
        "acceptable departments were written before the first run and were not used to tune the retriever",
        "([mandate_knowledge_base.md](mandate_knowledge_base.md) section 5). Ten queries are an inspection,",
        "not an evaluation: the Phase 8 labelled set is for that.",
        "",
    ]
    summary = {m: [0, 0] for m in retrievers}
    detail = []
    for i, (lang, query, expected) in enumerate(CHECK_QUERIES, start=1):
        detail += [f"### Q{i} ({lang})", "", f"> {query}", "", f"Acceptable: {', '.join(expected)}", ""]
        for m, r in retrievers.items():
            top = r.search(query, 3)
            keys = [c.ministry_key for c in top]
            summary[m][0] += keys[0] in expected
            summary[m][1] += any(k in expected for k in keys)
            detail.append(f"**{m}**")
            detail.append("")
            for rank, c in enumerate(top, start=1):
                mark = " ✓" if c.ministry_key in expected else ""
                best = c.evidence[0][1]
                src = best.citation if best.official else f"project-authored {best.kind}"
                detail.append(
                    f"{rank}. {c.canonical_name}{mark} (score {c.score:.3f}); best evidence: "
                    f'"{best.text[:90]}" [{src}]'
                )
            detail.append("")
    lines += ["| Method | Correct at rank 1 | Correct in top 3 |", "|---|---|---|"]
    lines += [
        f"| {m} | {a} / {len(CHECK_QUERIES)} | {b} / {len(CHECK_QUERIES)} |" for m, (a, b) in summary.items()
    ]
    lines += ["", *detail]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("query", nargs="?", help="grievance text (not logged or stored)")
    p.add_argument("--method", choices=["lexical", "dense", "hybrid"], default="lexical")
    p.add_argument("--k", type=int, default=3)
    p.add_argument("--check", action="store_true", help="run the 10 fixed test queries")
    p.add_argument("--methods", default="lexical,dense,hybrid", help="methods for --check (comma-separated)")
    args = p.parse_args(argv)
    if args.check:
        CHECK_MD.write_text(run_check(args.methods.split(",")), encoding="utf-8")
        print(f"Wrote {CHECK_MD.relative_to(PROJECT_ROOT)}")
        return 0
    if not args.query:
        p.error("give a query or --check")
    for rank, c in enumerate(make_retriever(args.method).search(args.query, args.k), start=1):
        print(f"{rank}. {c.canonical_name}  score={c.score:.3f}")
        for s, ch in c.evidence:
            tag = ch.citation if ch.official else f"project-authored {ch.kind}"
            print(f"     {s:.3f}  {ch.text[:100]}  [{tag}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
