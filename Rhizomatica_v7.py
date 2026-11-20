#!/usr/bin/env python3
"""Build an interactive multifacet network from ead.xml.

Features:
- Multi-select facet spotlight with intersection filtering.
- Timeline slider filter.
- File list on the right.
- Hover/click file nodes or file list items to show a detail card below the
  facet spotlight area.

Data model:
- Every EAD hierarchy node (<c01>, <c02>, ...) with no <c0N> children becomes
  a row (a file).
- Every node that DOES have children becomes a facet, and all of its
  descendant files are tagged with it (and every facet above it).
- Words and phrases in file titles, general notes, scope-and-content text,
  and hierarchy facet titles become keyword facets. EAD <persname> entries
  also seed person-name matching.

Input:
- Files named <prefix>-ead.xml in the input/ folder.
- config.json (optional) in the same folder, for facet_redundancy_ratio.

Output:
- Files named <prefix>-rhizome.html in the output/ folder.
"""

from __future__ import annotations

import argparse
import calendar
from html import escape as escape_html
import json
import math
import re
import webbrowser
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date
from pathlib import Path

INPUT_DIR = Path(__file__).with_name("input")
CONFIG_FILE = Path(__file__).with_name("config.json")
OUTPUT_DIR = Path(__file__).with_name("output")

DEFAULT_CONFIG = {
    "facet_redundancy_ratio": 0.7,
}

# Stopwords affect keyword candidate selection and acronym matching, not the
# hierarchy labels read from series titles.
STOPWORDS = frozenset(
    """
    a an the and or but if then than so of in on at by for to from with
    as is are was were be been being this that these those it its their
    his her you your our we they he she i not no nor too very can will
    just should now into during about above below between after before
    over under again further once here there when where why how all any
    both each few more most other some such only own same other include includes also see consists consist related relates
    material materials series sub-series sub additionally moreover beyond circa included many specifically specific et al
    i ii iii iv v vi vii viii ix x xi xii xiii xiv xv xvi xvii xviii xix xx etc
    vol
    """.split()
)

# Common archival/business vocabulary that is capitalized only because
# titles are written in title case, not because it names an entity (e.g.
# "Meeting", "Report", "Trip"). Single-word candidates in this list are
# dropped unless they also show up written in ALL CAPS somewhere (making
# them look like an acronym rather than a generic word) -- see
# extract_keyword_facets. Multi-word phrases are never affected by this
# list, since a recurring multi-word phrase is distinctive regardless of
# how common its individual words are.
GENERIC_WORDS = frozenset(
    """
    inc inc. file files digital physical Physical Digital material materials series sub-series sub part parts
    """.split()
)

C_LEVEL_RE = re.compile(r"^c\d*$")
WORD_RE = re.compile(r"[a-zA-Z'’]+")
KEYWORD_WORD_RE = re.compile(r"[a-zA-Z0-9'’]+(?:-[a-zA-Z0-9'’]+)*")
SEGMENT_SPLIT_RE = re.compile(r"[,:;()\[\]\"/]+|\.\s+")
# Dotted abbreviations like "U.S." or "U.S.A." have no space between the
# periods, so they'd otherwise tokenize as separate single letters ("U",
# "S") that never meet the single-word length filter. Collapse them to
# "US" / "USA" before tokenizing so they're recognized as one acronym --
# and so they match up with "US"/"United States" elsewhere in the title.
ABBREVIATION_RE = re.compile(r"\b(?:[A-Za-z]\.){2,}(?![A-Za-z])")
PARENTHETICAL_ACRONYM_RE = re.compile(r"\(([A-Z][A-Z.]{1,})\)")
AO_URI_RE = re.compile(r"/archival_objects/(\d+)")
EXTENT_NUMBER_RE = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
MAX_NGRAM = 4
MAX_ACRONYM_EXPANSION_NGRAM = 8
COLLECTION_TITLE_DESCRIPTORS = frozenset({"record", "paper", "collection"})


def load_config():
    config = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            loaded = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                config.update(loaded)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"Warning: could not read {CONFIG_FILE.name} ({exc}); using defaults.")
    return config


def local_tag(elem):
    tag = elem.tag
    return tag.split("}", 1)[1] if "}" in tag else tag


def strip_namespaces(root):
    for elem in root.iter():
        elem.tag = local_tag(elem)
    return root


def text_of(elem):
    if elem is None:
        return ""
    return "".join(elem.itertext()).strip()


def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug or "facet"


def parse_normal_date(token, is_end):
    token = token.strip()
    if not token:
        return ""
    parts = token.split("-")
    try:
        year = int(parts[0])
    except ValueError:
        return ""
    if len(parts) >= 3:
        month, day = int(parts[1]), int(parts[2])
    elif len(parts) == 2:
        month = int(parts[1])
        day = calendar.monthrange(year, month)[1] if is_end else 1
    else:
        month, day = (12, 31) if is_end else (1, 1)
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def extract_did_fields(node):
    did = node.find("did") if local_tag(node) != "did" else node
    if did is None:
        for child in node:
            if local_tag(child) == "did":
                did = child
                break

    unittitle = ""
    date_exprs = []
    normal = ""
    container_instance = ""
    extent_value = None
    extent_texts = []
    if did is not None:
        for child in did:
            tag = local_tag(child)
            if tag == "unittitle":
                unittitle = text_of(child)
            elif tag == "unitdate":
                date_expr = text_of(child)
                if date_expr:
                    date_exprs.append(date_expr)
                normal = child.get("normal", "")
            elif tag == "container":
                label = child.get("label", "").strip()
                number = text_of(child)
                container_instance = f"{label} {number}".strip()
            elif tag in {"extent", "physdesc"}:
                extent_elements = (child,) if tag == "extent" else child.iter()
                for extent in extent_elements:
                  if local_tag(extent) != "extent":
                    continue
                  extent_text = text_of(extent)
                  if extent_text:
                    extent_texts.append(extent_text)
                  match = EXTENT_NUMBER_RE.search(extent_text)
                  if match and extent_value is None:
                    extent_value = float(match.group().replace(",", ""))

    begin, end = "", ""
    if normal:
        half = normal.split("/")
        begin = parse_normal_date(half[0], is_end=False)
        end = parse_normal_date(half[1] if len(half) > 1 else half[0], is_end=True)

    date_expr = ", ".join(date_exprs)
    if date_expr:
        display_date = date_expr
    elif begin and end and begin != end:
        display_date = f"{begin} to {end}"
    elif begin:
        display_date = begin
    else:
        display_date = "undated"

    return {
        "title": unittitle,
        "date_expr": date_expr,
        "begin": begin,
        "end": end or begin,
        "display_date": display_date,
        "container_instance": container_instance,
        "extent_value": extent_value,
        "extents": extent_texts,
    }


def own_text(node, tag_name):
    for child in node:
        if local_tag(child) == tag_name:
            return " ".join(p.strip() for p in child.itertext()).strip()
    return ""


def unitid_facet_id(node, fallback_title):
    for child in node:
        if local_tag(child) == "did":
            for grand in child:
                if local_tag(grand) == "unitid" and grand.get("type") == "aspace_uri":
                    match = AO_URI_RE.search(text_of(grand))
                    if match:
                        return f"ao_{match.group(1)}"
    return slugify(fallback_title)


def hierarchy_facet_id(node, fallback_title, facets_by_id):
    base_id = unitid_facet_id(node, fallback_title)
    facet_id = f"series_{base_id}"
    suffix = 2
    while facet_id in facets_by_id:
        facet_id = f"series_{base_id}_{suffix}"
        suffix += 1
    return facet_id


def walk(
    node, ancestor_facets, inherited_date, rows, row_ids, facets_by_id,
    hierarchy_parent_by_id, counter
):
    children = [child for child in node if C_LEVEL_RE.match(local_tag(child))]
    did_fields = extract_did_fields(node)
    has_title = bool(did_fields["title"])
    has_own_date = bool(did_fields["date_expr"] or did_fields["begin"])

    if not children:
        counter[0] += 1
        base_row_id = unitid_facet_id(node, did_fields["title"] or f"file_{counter[0]}")
        row_id = base_row_id
        suffix = 2
        while row_id in row_ids:
            row_id = f"{base_row_id}_{suffix}"
            suffix += 1
        row_ids.add(row_id)
        file_title = did_fields["title"]
        # Fall back to the nearest untitled-but-dated ancestor's date only
        # when this file has none of its own.
        date_fields = did_fields if has_own_date else (inherited_date or did_fields)
        rows.append(
            {
                "id": row_id,
                "title": file_title,
                "display_date": date_fields["display_date"],
                "date_expr": date_fields["date_expr"],
                "begin": date_fields["begin"],
                "end": date_fields["end"],
                "container_instance": did_fields["container_instance"],
                "box": f"[{did_fields['container_instance']}]" if did_fields["container_instance"] else "",
                "general_note": own_text(node, "odd"),
                "scope_content": own_text(node, "scopecontent"),
                "physical_desc": own_text(node, "physdesc"),
                "extent_value": did_fields["extent_value"],
                "extents": did_fields["extents"],
                "facets": list(ancestor_facets),
            }
        )
        return

    # An untitled series/subseries that only carries a date isn't a
    # meaningful facet (nothing to label it with) -- skip faceting it, but
    # pass its date down so dateless files beneath it still get one.
    skip_as_facet = has_own_date and not has_title
    if skip_as_facet:
        next_ancestor_facets = ancestor_facets
        next_inherited_date = did_fields
    else:
        facet_id = hierarchy_facet_id(node, did_fields["title"] or f"series_{len(facets_by_id) + 1}", facets_by_id)
        facets_by_id[facet_id] = did_fields["title"] or facet_id
        hierarchy_parent_by_id[facet_id] = ancestor_facets[-1] if ancestor_facets else None
        next_ancestor_facets = ancestor_facets + [facet_id]
        next_inherited_date = inherited_date

    for child in children:
        walk(
            child, next_ancestor_facets, next_inherited_date, rows, row_ids,
            facets_by_id, hierarchy_parent_by_id, counter
        )


def normalize_abbreviations(text):
    return ABBREVIATION_RE.sub(lambda m: m.group(0).replace(".", ""), text)


def tokenize_title_segments(title):
    """Split a title into word-sequences, breaking at punctuation so n-grams
    never bridge across it (e.g. "Guide, UM" in "...Guide, UM EXPRES 1.0")."""
    title = normalize_abbreviations(title)
    segments = []
    for chunk in SEGMENT_SPLIT_RE.split(title):
        words = KEYWORD_WORD_RE.findall(chunk)
        if words:
            segments.append(words)
    return segments


def tokenize_row_segments(row):
    for field in ("title", "general_note", "scope_content"):
        yield from tokenize_title_segments(row.get(field) or "")


def parenthetical_acronym_expansions(text):
    for match in PARENTHETICAL_ACRONYM_RE.finditer(text):
        segments = tokenize_title_segments(text[:match.start()])
        if not segments:
            continue
        words = segments[-1]
        if not 2 <= len(words) <= MAX_ACRONYM_EXPANSION_NGRAM:
            continue
        expansion = tuple(
            normalize_keyword_word(word).lower() for word in words
        )
        acronym = normalize_abbreviations(match.group(1)).lower()
        if acronym.isalpha() and len(acronym) >= 2 and all(expansion):
            yield acronym, expansion


IRREGULAR_SINGULARS = {
    "analyses": "analysis",
    "children": "child",
    "crises": "crisis",
    "criteria": "criterion",
    "diagnoses": "diagnosis",
    "feet": "foot",
    "geese": "goose",
    "halves": "half",
    "hypotheses": "hypothesis",
    "indices": "index",
    "knives": "knife",
    "leaves": "leaf",
    "lives": "life",
    "matrices": "matrix",
    "men": "man",
    "mice": "mouse",
    "oxen": "ox",
    "people": "person",
    "phenomena": "phenomenon",
    "series": "series",
    "shoes": "shoe",
    "species": "species",
    "teeth": "tooth",
    "theses": "thesis",
    "toes": "toe",
    "vertices": "vertex",
    "women": "woman",
    "news": "news",
    "politics": "politics",
    "mathematics": "mathematics",
    "physics": "physics",
    "economics": "economics",
    "ethics": "ethics",
    "linguistics": "linguistics",
    "athletics": "athletics",
    "statistics": "statistics",
}


def normalize_keyword_word(word):
    """Normalize English possessives and common noun plurals to singular."""
    if "-" in word:
        return "-".join(normalize_keyword_word(part) for part in word.split("-"))

    base = word
    has_singular_possessive = base.lower().endswith(("'s", "’s"))
    if has_singular_possessive:
        base = base[:-2]
    elif base.endswith(("'", "’")):
        base = base[:-1]

    lowered = base.lower()
    if has_singular_possessive and lowered.endswith("s"):
        singular = lowered
    elif base.isupper():
        singular = lowered
    elif base.endswith("s") and base[:-1].isupper():
        singular = base[:-1].lower()
    elif lowered in IRREGULAR_SINGULARS:
        singular = IRREGULAR_SINGULARS[lowered]
    elif lowered.endswith("ies") and len(lowered) > 4:
        singular = lowered[:-3] + "y"
    elif lowered.endswith(("ches", "shes", "sses", "xes", "zes")):
        singular = lowered[:-2]
    elif lowered.endswith("oes"):
        singular = lowered[:-2]
    elif lowered.endswith("s") and not lowered.endswith(("ss", "us", "is")):
        singular = lowered[:-1]
    else:
        singular = lowered

    if base.isupper() or (len(base) > 2 and base[:2].isupper()):
        return singular.upper()
    if base[0:1].isupper():
        return singular.capitalize()
    return singular


def parse_person_name(name):
    name = " ".join(name.split())
    parentheticals = re.findall(r"\(([^()]*)\)", name)
    unparenthesized = re.sub(r"\s*\([^()]*\)", "", name).strip(" ,.;")
    parts = [part.strip() for part in unparenthesized.split(",")]

    if len(parts) > 1:
        family = WORD_RE.findall(parts[0])
        given = WORD_RE.findall(parts[1])
    else:
        words = WORD_RE.findall(unparenthesized)
        if words and words[-1].casefold().rstrip(".") in {"jr", "sr", "ii", "iii", "iv"}:
            words = words[:-1]
        if len(words) < 2:
            return None
        given, family = words[:-1], words[-1:]

    if not family or not given:
        return None

    given_forms = [given]
    for parenthetical in parentheticals:
        expanded = WORD_RE.findall(parenthetical)
        if not expanded:
            continue
        same_first = expanded[0].casefold() == given[0].casefold()
        initial_expansion = (
            len(given[0]) == 1
            and expanded[0].casefold().startswith(given[0].casefold())
        )
        if same_first or initial_expansion:
            given_forms.append(expanded)

    return family, given_forms


def build_person_name_index(person_names):
    groups = {}
    for name in person_names:
        parsed = parse_person_name(name)
        if parsed is None:
            continue
        family, given_forms = parsed
        first = normalize_keyword_word(given_forms[0][0]).lower()
        normalized_family = tuple(
            normalize_keyword_word(word).lower() for word in family
        )
        identity = (first, normalized_family)
        group = groups.setdefault(
            identity, {"family": family, "given_forms": []}
        )
        group["given_forms"].extend(given_forms)

    aliases = defaultdict(set)
    for group in groups.values():
        given_forms = group["given_forms"]
        preferred_given = max(
            given_forms,
            key=lambda words: (
                len(words[0]) > 1,
                sum(len(word) > 1 for word in words[1:]),
                len(words),
                sum(len(word) for word in words),
            ),
        )
        family = group["family"]
        person_key = tuple(
            normalize_keyword_word(word).lower()
            for word in preferred_given + family
        )

        for given in given_forms:
            first, middle = given[0], given[1:]
            middle_variants = {()}
            if len(middle) <= 5:
                for word in middle:
                    expanded_variants = set()
                    for variant in middle_variants:
                        expanded_variants.add(variant)
                        expanded_variants.add(variant + (word,))
                        if len(word) > 1:
                            expanded_variants.add(variant + (word[0],))
                    middle_variants = expanded_variants
            else:
                middle_variants.update(
                    {
                        tuple(middle),
                        tuple(word[0] for word in middle),
                    }
                )

            for middle_variant in middle_variants:
                given_variant = (
                    normalize_keyword_word(first).lower(),
                    *(
                        normalize_keyword_word(word).lower()
                        for word in middle_variant
                    ),
                )
                aliases[given_variant + person_key[len(preferred_given):]].add(person_key)
                aliases[person_key[len(preferred_given):] + given_variant].add(person_key)

    aliases_by_first = defaultdict(list)
    for alias, person_keys in aliases.items():
        aliases_by_first[alias[0]].append((alias, person_keys))
    for candidates in aliases_by_first.values():
        candidates.sort(key=lambda item: (-len(item[0]), item[0]))
    return aliases, aliases_by_first


def match_person_names(source, aliases_by_first):
    matches = []
    for field in ("title", "general_note", "scope_content"):
        text = source.get(field) or ""
        word_matches = list(WORD_RE.finditer(text))
        tokens = [
            normalize_keyword_word(word).lower()
            for word in (match.group() for match in word_matches)
        ]
        for start, first in enumerate(tokens):
            matches_at_start = {}
            for alias, person_keys in aliases_by_first.get(first, ()):
                if tuple(tokens[start:start + len(alias)]) == alias:
                    for person_key in person_keys:
                        if person_key not in matches_at_start:
                            surface = text[
                                word_matches[start].start():
                                word_matches[start + len(alias) - 1].end()
                            ]
                            matches_at_start[person_key] = (alias, surface)
            matches.extend(
                (person_key, surface)
                for person_key, (_, surface) in matches_at_start.items()
            )
    return matches


def ngram_facet_id(chunk):
    parts = [re.sub(r"[^a-z0-9-]+", "", w) or "x" for w in chunk]
    return "kw_" + "_".join(parts)


def is_sub_ngram(short_chunk, long_chunk):
    """True if short_chunk's words appear as a contiguous run inside long_chunk."""
    short_len, long_len = len(short_chunk), len(long_chunk)
    if short_len >= long_len:
        return False
    return any(long_chunk[i:i + short_len] == short_chunk for i in range(long_len - short_len + 1))


def merge_redundant_facets(final_chunks, redundancy_ratio):
    """Collapse a facet into whichever other facet contains (or is contained
    by) it -- directly, not transitively -- when it's clearly just a minor
    variant mention: its file count is under redundancy_ratio of that other
    facet's count. Its files are folded into the surviving facet rather than
    dropped, so e.g. "UM EXPRES" and "EXPRES Project" both collapse into the
    dominant "EXPRES" facet instead of fragmenting it. Single-word facets
    are not collapsed into longer phrases."""
    counts = {chunk: len(row_ids) for chunk, row_ids in final_chunks.items()}
    chunks = list(counts)

    drop_target = {}
    for chunk in chunks:
        dominant = None
        for other in chunks:
            if other == chunk or counts[other] <= counts[chunk]:
                continue
            if len(chunk) == 1 and len(other) > 1:
                continue
            if is_sub_ngram(chunk, other) or is_sub_ngram(other, chunk):
                if dominant is None or counts[other] > counts[dominant]:
                    dominant = other
        if dominant is not None and counts[chunk] < redundancy_ratio * counts[dominant]:
            drop_target[chunk] = dominant

    def resolve(chunk):
        seen = set()
        while chunk in drop_target and chunk not in seen:
            seen.add(chunk)
            chunk = drop_target[chunk]
        return chunk

    merged = defaultdict(set)
    for chunk, row_ids in final_chunks.items():
        merged[resolve(chunk)] |= row_ids
    return dict(merged), {chunk: resolve(chunk) for chunk in chunks}


def extract_keyword_facets(
    rows, min_file_count, redundancy_ratio, collection_title="",
    hierarchy_titles=(), person_names=()
):
    """Find recurring terms in file text and hierarchy titles.

    Possessives and common English noun plurals are normalized before
    candidates are counted and matched, so variants share a facet labeled
    with the most frequently occurring original form. EAD person names seed
    aliases for first-last, last-first, and middle-name variants.

    - Multi-word phrases (e.g. "Ann Arbor", "User's Reference Guide",
      "Project Management Committee") are treated as entities outright --
      a recurring phrase is distinctive no matter how common its
      individual words are.
    - Single words are entities too when they look like an acronym
      (written ALL CAPS somewhere, e.g. NSF, CMU, EXPRES) or aren't
      generic archival/business vocabulary (GENERIC_WORDS). A generic
      word like "Meeting" or "Report" only becomes a facet as part of a
      qualifying phrase, never standing alone.
    - Among qualifying candidates, the longest phrase at each spot wins,
      when initially assigning rows (see MAX_NGRAM). Independently
      qualifying single-word facets remain separate from longer phrases.
    """
    min_file_count = max(2, min_file_count)
    candidate_rows = defaultdict(set)
    candidate_label_counts = defaultdict(lambda: defaultdict(int))
    candidate_label_spellings = {}
    extended_expansion_rows = defaultdict(lambda: defaultdict(set))
    acronym_seen = set()
    acronym_occurrence_rows = defaultdict(set)
    collection_title_chunks = set()
    person_aliases, person_aliases_by_first = build_person_name_index(
        person_names
    )
    for words in tokenize_title_segments(collection_title):
        normalized = [normalize_keyword_word(word).lower() for word in words]
        for length in range(1, len(normalized) + 1):
            for i in range(len(normalized) - length + 1):
                collection_title_chunks.add(tuple(normalized[i:i + length]))
        title_name_words = list(normalized)
        while title_name_words and title_name_words[-1] in COLLECTION_TITLE_DESCRIPTORS:
            title_name_words.pop()
        for title_form in (normalized, title_name_words):
            title_initials = facet_initials(" ".join(title_form))
            if len(title_initials) >= 2:
                collection_title_chunks.add((title_initials,))
    collection_title_chunks.update(
        person_key
        for person_key, _ in match_person_names(
            {"title": collection_title}, person_aliases_by_first
        )
    )

    text_sources = [
        (row, {row["id"]}, None)
        for row in rows
    ] + [
        ({"title": hierarchy["title"]}, hierarchy["row_ids"], hierarchy["id"])
        for hierarchy in hierarchy_titles
        if hierarchy["row_ids"]
    ]

    def add_candidate(chunk, source_row_ids, surface_label):
        candidate_rows[chunk].update(source_row_ids)
        surface_key = surface_label.casefold()
        candidate_label_counts[chunk][surface_key] += 1
        candidate_label_spellings.setdefault((chunk, surface_key), surface_label)

    person_source_matches = []
    for source, source_row_ids, _ in text_sources:
        person_matches = match_person_names(source, person_aliases_by_first)
        person_source_matches.append({person_key for person_key, _ in person_matches})
        for person_key, surface_label in person_matches:
            add_candidate(person_key, source_row_ids, surface_label)

        for field in ("title", "general_note", "scope_content"):
            for acronym, expansion in parenthetical_acronym_expansions(
                source.get(field) or ""
            ):
                extended_expansion_rows[acronym][expansion].update(
                    source_row_ids
                )

        for words in tokenize_row_segments(source):
            normalized_words = [
                (normalize_keyword_word(word), word) for word in words
            ]
            normalized_words = [
                (normalized, surface)
                for normalized, surface in normalized_words
                if normalized
            ]
            normalized = [normalized for normalized, _ in normalized_words]
            surface_words = [surface for _, surface in normalized_words]
            lowered = [word.lower() for word in normalized]
            n = len(normalized)
            for i, word in enumerate(normalized):
                if word.isupper() and len(word) >= 2:
                    acronym_seen.add(lowered[i])
                    acronym_occurrence_rows[lowered[i]].update(source_row_ids)
            for length in range(1, MAX_NGRAM + 1):
                for i in range(0, n - length + 1):
                    chunk = tuple(lowered[i:i + length])
                    if chunk[0] in STOPWORDS or chunk[-1] in STOPWORDS:
                        continue
                    if length == 1:
                        # Short acronyms (US, UM, UK...) are only let through
                        # at 2 letters when the source actually wrote them in
                        # caps; otherwise this would admit ordinary 2-letter
                        # fragments missed by the stopword list.
                        min_len = 2 if normalized[i].isupper() else 3
                        if len(chunk[0]) < min_len:
                            continue
                    surface_label = " ".join(surface_words[i:i + length])
                    person_keys = person_aliases.get(chunk)
                    if not person_keys:
                        add_candidate(chunk, source_row_ids, surface_label)
            for length in range(MAX_NGRAM + 1, min(MAX_ACRONYM_EXPANSION_NGRAM, n) + 1):
                for i in range(0, n - length + 1):
                    chunk = tuple(lowered[i:i + length])
                    acronym = facet_initials(" ".join(normalized[i:i + length]))
                    if len(acronym) >= 2:
                        extended_expansion_rows[acronym][chunk].update(source_row_ids)

    candidate_label = {
        chunk: candidate_label_spellings[(chunk, max(forms, key=forms.get))]
        for chunk, forms in candidate_label_counts.items()
    }

    def is_entity(chunk):
        if len(chunk) > 1:
            return True
        word = chunk[0]
        return word in acronym_seen or word not in GENERIC_WORDS

    qualifying = {
        chunk
        for chunk, row_ids in candidate_rows.items()
        if (
            len(row_ids) >= min_file_count
            and is_entity(chunk)
            and sum(word.isdigit() for word in chunk) * 2 <= len(chunk)
            and chunk not in collection_title_chunks
        )
    }

    facet_rows = defaultdict(set)
    hierarchy_match_rows = defaultdict(lambda: defaultdict(set))
    file_match_rows = defaultdict(set)
    for source_index, (source, source_row_ids, hierarchy_id) in enumerate(text_sources):
        matched_chunks = set(person_source_matches[source_index])
        for words in tokenize_row_segments(source):
            lowered = [normalize_keyword_word(word).lower() for word in words]
            lowered = [word for word in lowered if word]
            n = len(lowered)
            i = 0
            while i < n:
                step = 1
                for length in range(min(MAX_NGRAM, n - i), 0, -1):
                    chunk = tuple(lowered[i:i + length])
                    person_keys = person_aliases.get(chunk)
                    if person_keys:
                        matched_person_keys = person_keys & qualifying
                        if matched_person_keys:
                            matched_chunks.update(matched_person_keys)
                            step = length
                            break
                        continue
                    if chunk in qualifying:
                        matched_chunks.add(chunk)
                        step = length
                        break
                i += step
        for chunk in matched_chunks:
            facet_rows[chunk].update(source_row_ids)
            if hierarchy_id is not None:
                hierarchy_match_rows[chunk][hierarchy_id].update(source_row_ids)
            else:
                file_match_rows[chunk].update(source_row_ids)

    excluded_component_rows = defaultdict(set)
    expansions_by_acronym = defaultdict(dict)
    for expansion_chunk, expansion_row_ids in candidate_rows.items():
        if len(expansion_chunk) >= 2:
            acronym = facet_initials(candidate_label[expansion_chunk])
            expansions_by_acronym[acronym][expansion_chunk] = expansion_row_ids
    for acronym, expansions in extended_expansion_rows.items():
        expansions_by_acronym[acronym].update(expansions)

    for acronym, expansions in expansions_by_acronym.items():
        matching_acronym_rows = acronym_occurrence_rows.get(acronym)
        if not matching_acronym_rows:
            continue

        for expansion_chunk, expansion_row_ids in expansions.items():
            matching_rows = matching_acronym_rows & expansion_row_ids
            if not matching_rows:
                continue
            for start in range(len(expansion_chunk)):
                for end in range(start + 1, len(expansion_chunk) + 1):
                    component = expansion_chunk[start:end]
                    if component != expansion_chunk:
                        excluded_component_rows[component].update(matching_rows)

    for chunk, row_ids in facet_rows.items():
        row_ids.difference_update(excluded_component_rows.get(chunk, set()))
    for chunk, row_ids in file_match_rows.items():
        row_ids.difference_update(excluded_component_rows.get(chunk, set()))

    final_chunks = {chunk: row_ids for chunk, row_ids in facet_rows.items() if len(row_ids) >= min_file_count}
    for chunk, row_ids in list(final_chunks.items()):
        hierarchy_ids = {
            hierarchy_id
            for hierarchy_id, matched_row_ids in hierarchy_match_rows.get(chunk, {}).items()
            if row_ids.intersection(matched_row_ids)
        }
        direct_file_row_ids = row_ids.intersection(file_match_rows.get(chunk, ()))
        if len(hierarchy_ids) == 1 and not direct_file_row_ids:
            del final_chunks[chunk]

    final_chunks, chunk_remap = merge_redundant_facets(final_chunks, redundancy_ratio)

    rows_by_id = {row["id"]: row for row in rows}
    keyword_facets = {}
    keyword_hierarchy_links = defaultdict(set)
    keyword_file_matches = defaultdict(set)
    for chunk, hierarchy_matches in hierarchy_match_rows.items():
        final_chunk = chunk_remap.get(chunk)
        if final_chunk not in final_chunks:
            continue
        for hierarchy_id, matched_row_ids in hierarchy_matches.items():
            if final_chunks[final_chunk].intersection(matched_row_ids):
                keyword_hierarchy_links[final_chunk].add(hierarchy_id)
    for chunk, row_ids in file_match_rows.items():
        final_chunk = chunk_remap.get(chunk)
        if final_chunk in final_chunks:
            keyword_file_matches[final_chunk].update(
                final_chunks[final_chunk].intersection(row_ids)
            )

    for chunk, hierarchy_ids in list(keyword_hierarchy_links.items()):
        if len(hierarchy_ids) == 1 and not keyword_file_matches.get(chunk):
            final_chunks.pop(chunk, None)
            del keyword_hierarchy_links[chunk]

    for chunk, row_ids in final_chunks.items():
        facet_id = ngram_facet_id(chunk)
        keyword_facets[facet_id] = candidate_label[chunk]
        for row_id in row_ids:
            rows_by_id[row_id]["facets"].append(facet_id)

    return (
        keyword_facets,
        {
            ngram_facet_id(chunk): hierarchy_ids
            for chunk, hierarchy_ids in keyword_hierarchy_links.items()
        },
        {
            ngram_facet_id(chunk): row_ids
            for chunk, row_ids in keyword_file_matches.items()
        },
    )


def facet_initials(label):
    """Initials of a label's significant words, e.g. "National Science
    Foundation" -> "nsf". Stopwords (of, the, for, ...) don't count, so
    "University of Michigan" -> "um" rather than "uom"."""
    words = WORD_RE.findall(label)
    significant = [w for w in words if w.lower() not in STOPWORDS]
    return "".join(w[0] for w in significant if w).lower()


def facet_canonical_key(facet_id, facets_by_id):
    """Sort key used whenever several facets merge into one: prefer a
    hierarchy (series) facet over a keyword one, since it's structurally
    meaningful rather than inferred; among same-type ties, prefer the
    more descriptive (longer) label; fall back to the id for determinism."""
    is_keyword = facet_id.startswith("kw_")
    label = facets_by_id.get(facet_id, facet_id)
    return (1 if is_keyword else 0, -len(label.split()), facet_id)


def apply_facet_remap(rows, facets_by_id, id_remap):
    """Rewrite every row's facet list to point merged-away ids at their
    canonical replacement (de-duplicating), then drop the merged ids."""
    if not id_remap:
        return {}

    for row in rows:
        deduped = []
        for facet_id in row["facets"]:
            target = id_remap.get(facet_id, facet_id)
            if target not in deduped:
                deduped.append(target)
        row["facets"] = deduped

    for facet_id in id_remap:
        facets_by_id.pop(facet_id, None)

    return id_remap


def remap_keyword_metadata(keyword_metadata, id_remap):
    if not id_remap:
        return

    remapped = defaultdict(set)
    for keyword_id, values in keyword_metadata.items():
        remapped[id_remap.get(keyword_id, keyword_id)].update(values)
    keyword_metadata.clear()
    keyword_metadata.update(remapped)


def normalize_label(label):
    return re.sub(r"\s+", " ", label.strip().lower())


def propagate_embedded_facet_memberships(rows, facets_by_id):
  words_by_facet = {
    facet_id: tuple(KEYWORD_WORD_RE.findall(normalize_label(label)))
    for facet_id, label in facets_by_id.items()
    if facet_id.startswith("kw_")
  }
  sources_by_target = defaultdict(list)
  for source_id, source_words in words_by_facet.items():
    if not source_words:
      continue
    for target_id, target_words in words_by_facet.items():
      if source_id != target_id and is_sub_ngram(target_words, source_words):
        sources_by_target[target_id].append(source_id)

  if not sources_by_target:
    return

  rows_by_id = {row["id"]: row for row in rows}
  rows_by_facet = defaultdict(set)
  for row in rows:
    for facet_id in row["facets"]:
      rows_by_facet[facet_id].add(row["id"])

  for target_id, source_ids in sources_by_target.items():
    for source_id in source_ids:
      for row_id in rows_by_facet[source_id]:
        target_facets = rows_by_id[row_id]["facets"]
        if target_id not in target_facets:
          target_facets.append(target_id)


def merge_same_label_facets(rows, facets_by_id):
    """Merge duplicate keyword labels without merging source hierarchy nodes."""
    groups = defaultdict(list)
    for facet_id, label in facets_by_id.items():
        if facet_id.startswith("kw_"):
            groups[normalize_label(label)].append(facet_id)

    id_remap = {}
    for facet_ids in groups.values():
        if len(facet_ids) < 2:
            continue
        canonical = min(facet_ids, key=lambda fid: facet_canonical_key(fid, facets_by_id))
        for facet_id in facet_ids:
            if facet_id != canonical:
                id_remap[facet_id] = canonical

    return apply_facet_remap(rows, facets_by_id, id_remap)


def merge_acronym_facets(rows, facets_by_id):
    """Merge an acronym facet (e.g. "NSF") into the matching full-name
    phrase facet (e.g. "National Science Foundation") when the acronym's
    letters equal the initials of that phrase's significant words -- even
    though the two facets may have been built from entirely different
    files, since some titles use the initialism and others spell it out.
    The full name wins as canonical; files from the acronym are folded
    into it."""
    acronym_ids = [
        fid for fid, label in facets_by_id.items()
        if fid.startswith("kw_") and label.isalpha() and label.isupper() and len(label) >= 2
    ]
    phrase_ids = [fid for fid, label in facets_by_id.items() if fid.startswith("kw_") and " " in label]

    id_remap = {}
    for acronym_id in acronym_ids:
        acronym = facets_by_id[acronym_id].lower()
        matches = [pid for pid in phrase_ids if facet_initials(facets_by_id[pid]) == acronym]
        if not matches:
            continue
        canonical = min(matches, key=lambda pid: (-len(facets_by_id[pid].split()), pid))
        id_remap[acronym_id] = canonical

    return apply_facet_remap(rows, facets_by_id, id_remap)


def merge_duplicate_facets(rows, facets_by_id):
    """Merge keyword facets with identical file memberships, not hierarchy nodes."""
    row_sets_by_facet = defaultdict(set)
    for row in rows:
        for facet_id in row["facets"]:
            row_sets_by_facet[facet_id].add(row["id"])

    groups = defaultdict(list)
    for facet_id, row_ids in row_sets_by_facet.items():
        if facet_id.startswith("kw_"):
            groups[frozenset(row_ids)].append(facet_id)

    id_remap = {}
    for facet_ids in groups.values():
        if len(facet_ids) < 2:
            continue
        canonical = min(facet_ids, key=lambda fid: facet_canonical_key(fid, facets_by_id))
        for facet_id in facet_ids:
            if facet_id != canonical:
                id_remap[facet_id] = canonical

    return apply_facet_remap(rows, facets_by_id, id_remap)


def build_records(root, facet_redundancy_ratio):
    archdesc = root.find("archdesc")
    collection_title = text_of(archdesc.find("did/unittitle")) if archdesc is not None else "Finding Aid Network"
    person_names = [
        text_of(elem) for elem in root.iter()
        if local_tag(elem) == "persname" and text_of(elem)
    ]

    dsc = archdesc.find("dsc") if archdesc is not None else None
    rows = []
    row_ids = set()
    facets_by_id = {}
    hierarchy_parent_by_id = {}
    counter = [0]
    if dsc is not None:
        for child in dsc:
            if C_LEVEL_RE.match(local_tag(child)):
                walk(child, [], None, rows, row_ids, facets_by_id, hierarchy_parent_by_id, counter)

    rows_by_hierarchy_facet = defaultdict(set)
    for row in rows:
        for facet_id in row["facets"]:
            rows_by_hierarchy_facet[facet_id].add(row["id"])
    hierarchy_titles = [
        {
            "id": facet_id,
            "title": label,
            "row_ids": rows_by_hierarchy_facet[facet_id],
        }
        for facet_id, label in facets_by_id.items()
        if label and label != facet_id and rows_by_hierarchy_facet[facet_id]
    ]

    keyword_facets, keyword_hierarchy_links, keyword_file_matches = extract_keyword_facets(
        rows, 1, facet_redundancy_ratio, collection_title,
        hierarchy_titles, person_names
    )
    facets_by_id.update(keyword_facets)

    id_remap = merge_same_label_facets(rows, facets_by_id)
    remap_keyword_metadata(keyword_hierarchy_links, id_remap)
    remap_keyword_metadata(keyword_file_matches, id_remap)
    id_remap = merge_acronym_facets(rows, facets_by_id)
    remap_keyword_metadata(keyword_hierarchy_links, id_remap)
    remap_keyword_metadata(keyword_file_matches, id_remap)
    propagate_embedded_facet_memberships(rows, facets_by_id)
    id_remap = merge_duplicate_facets(rows, facets_by_id)
    remap_keyword_metadata(keyword_hierarchy_links, id_remap)
    remap_keyword_metadata(keyword_file_matches, id_remap)

    facet_counts = {facet_id: 0 for facet_id in facets_by_id}
    for row in rows:
        for facet_id in row["facets"]:
            facet_counts[facet_id] += 1

    facets = [
        {
            "id": facet_id,
            "label": label,
            "count": facet_counts.get(facet_id, 0),
            "kind": "keyword" if facet_id.startswith("kw_") else "hierarchy",
            "parent_id": hierarchy_parent_by_id.get(facet_id),
            "matched_hierarchy_ids": sorted(keyword_hierarchy_links.get(facet_id, ())),
            "direct_file_row_ids": sorted(
                keyword_file_matches.get(facet_id, ())
                if facet_id in keyword_hierarchy_links else ()
            ),
        }
        for facet_id, label in facets_by_id.items()
    ]

    return collection_title, rows, facets


def default_keyword_min_hits(facets, max_facets=30):
    keyword_counts = sorted(
        (facet["count"] for facet in facets if facet["kind"] == "keyword"),
        reverse=True,
    )
    if len(keyword_counts) <= max_facets:
        return 1
    return keyword_counts[max_facets] + 1


HTML_TEMPLATE = r'''<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>__TITLE__</title>
  <script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/noUiSlider/15.7.1/nouislider.min.js"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/noUiSlider/15.7.1/nouislider.min.css">
  <style>
    html, body {
      height: 100%;
    }
    body {
      margin: 0;
      font-family: Arial, Helvetica, sans-serif;
      background: #fff;
      color: #222;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    #controls {
      padding: 10px 14px;
      border-bottom: 1px solid #ddd;
      background: #f7f7f7;
      flex: 0 0 auto;
    }
    #title {
      font-size: 24px;
      font-weight: 700;
      margin-bottom: 8px;
      color: #222;
    }
    .toggle-row {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 6px;
      user-select: none;
      font-size: 13px;
    }
    #settingsMenu {
      position: fixed;
      top: 12px;
      right: 16px;
      z-index: 1000;
    }
    #settingsButton {
      background: rgba(255,255,255,0.88);
      border-radius: 8px;
      padding: 6px 10px;
      box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08);
      font-size: 13px;
      backdrop-filter: blur(6px);
    }
    #settingsPanel {
      position: absolute;
      top: calc(100% + 6px);
      right: 0;
      display: grid;
      gap: 12px;
      min-width: 220px;
      padding: 12px;
      border: 1px solid #ddd;
      border-radius: 8px;
      background: rgba(255,255,255,0.96);
      color: #222;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.14);
      backdrop-filter: blur(6px);
    }
    #settingsPanel[hidden] {
      display: none;
    }
    .settings-label {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 13px;
      cursor: pointer;
    }
    #keywordMinHits {
      box-sizing: border-box;
      width: 76px;
      padding: 4px 6px;
    }
    #keywordMinHitsHelp {
      margin: -6px 0 0;
      color: #555;
      font-size: 12px;
      line-height: 1.35;
    }
    #keywordMinHitsError {
      min-height: 1em;
      margin: -8px 0 0;
      color: #b42318;
      font-size: 12px;
    }

    body[data-theme="dark"] #settingsButton {
      background: rgba(30,30,30,0.88);
      color: #fff;
      box-shadow: 0 1px 4px rgba(0, 0, 0, 0.35);
    }

    body[data-theme="dark"] #settingsPanel {
      background: rgba(17,24,39,0.97);
      border-color: #475569;
      color: #e5e7eb;
    }

    body[data-theme="dark"] #keywordMinHitsHelp {
      color: #cbd5e1;
    }

    body[data-theme="dark"] #keywordMinHitsError {
      color: #fca5a5;
    }

    body[data-theme="dark"] #keywordMinHits {
      background: #0f172a;
      border: 1px solid #475569;
      color: #e5e7eb;
    }
    .facet-spotlight {
      display: inline-block;
      padding: 1px 6px;
      border-radius: 999px;
      background: #FFF2B3;
      border: 1px solid #FFB300;
      font-weight: 700;
    }
    #toolbar {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
      margin: 8px 0 6px 0;
    }
    .toolbar-btn {
      border: 1px solid #bbb;
      background: #fff;
      border-radius: 8px;
      padding: 4px 10px;
      font-size: 13px;
      cursor: pointer;
    }
    .toolbar-btn:disabled {
      opacity: 0.45;
      cursor: not-allowed;
    }
    #rangeText {
      margin-top: 6px;
      font-size: 13px;
      line-height: 1.35;
      color: #333;
    }
    #page {
      display: grid;
      grid-template-columns: minmax(210px, 0.78fr) minmax(0, 2.2fr) minmax(260px, 0.95fr);
      grid-template-rows: minmax(130px, 150px) minmax(0, 1fr);
      grid-template-areas:
        "facets dates related"
        "facets network related";
      gap: 10px;
      width: 100%;
      flex: 1 1 auto;
      min-height: 0;
      padding: 10px;
      box-sizing: border-box;
    }
    #facetSidebar, #dateSidebar, #rowSidebar, #detailSidebar {
      background: #fafafa;
      padding: 12px;
      box-sizing: border-box;
      overflow: auto;
      min-width: 0;
      min-height: 0;
      border: 1px solid #ddd;
      border-radius: 6px;
    }
    #facetSidebar {
      grid-area: facets;
      height: 100%;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    #dateSidebar {
      grid-area: dates;
      height: 100%;
    }
    #rowSidebar {
      flex: 1 1 0;
      min-height: 120px;
    }
    #rightSidebar {
      grid-area: related;
      display: flex;
      flex-direction: column;
      gap: 10px;
      min-width: 0;
      min-height: 0;
      height: 100%;
      overflow: hidden;
    }
    #detailSidebar {
      display: flex;
      flex-direction: column;
      flex: 0 1 auto;
      gap: 6px;
      min-height: 150px;
      max-height: 58%;
      min-width: 0;
      overflow: hidden;
    }
    #container {
      grid-area: network;
      width: 100%;
      height: 100%;
      min-width: 0;
      min-height: 0;
    }
    #facetHeader, #rowHeader, #detailHeader, #timelineHeader {
      font-size: 13px;
      font-weight: 700;
      margin: 8px 0;
      color: #333;
    }
    .panel-heading {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 8px;
    }
    .panel-heading #facetHeader {
      margin: 0;
    }
    .panel-heading .toolbar-btn {
      flex: 0 0 auto;
      width: auto !important;
    }
    #facetSections {
      display: flex;
      flex: 1 1 0;
      flex-direction: column;
      gap: 8px;
      min-height: 0;
    }
    .facet-section {
      display: flex;
      flex-direction: column;
      flex: 1 1 0;
      min-height: 0;
      overflow: hidden;
      border: 1px solid #e0e0e0;
      border-radius: 6px;
    }
    .facet-section-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 6px;
      flex: 0 0 auto;
      margin: 0;
      padding: 7px 8px;
      border-bottom: 1px solid #e0e0e0;
    }
    .facet-section-title {
      display: flex;
      align-items: center;
      gap: 5px;
      min-width: 0;
      margin: 0;
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: #555;
    }
    .facet-section-count {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 1.6em;
      padding: 1px 5px;
      border-radius: 999px;
      background: #e9edf2;
      color: #555;
      font-size: 10px;
      letter-spacing: 0;
    }
    .facet-sort-control {
      display: flex;
      flex: 0 0 auto;
      align-items: center;
      gap: 4px;
      font-size: 10px;
      text-transform: none;
      white-space: nowrap;
    }
    .facet-sort-option {
      color: #777;
      transition: color 0.15s ease;
    }
    .facet-sort-option.active {
      color: #333;
      font-weight: 700;
    }
    .facet-sort-switch {
      position: relative;
      display: inline-flex;
      flex: 0 0 auto;
      width: 28px;
      height: 16px;
      cursor: pointer;
    }
    .facet-sort-switch input {
      position: absolute;
      width: 1px;
      height: 1px;
      margin: 0;
      opacity: 0;
    }
    .facet-sort-switch-track {
      position: absolute;
      inset: 0;
      border-radius: 999px;
      background: #cbd2da;
      transition: background 0.15s ease;
    }
    .facet-sort-switch-track::before {
      position: absolute;
      top: 2px;
      left: 2px;
      width: 12px;
      height: 12px;
      border-radius: 50%;
      background: #fff;
      content: "";
      transition: transform 0.15s ease;
    }
    .facet-sort-switch input:checked + .facet-sort-switch-track {
      background: #7952a3;
    }
    .facet-sort-switch input:checked + .facet-sort-switch-track::before {
      transform: translateX(12px);
    }
    .facet-sort-switch input:focus-visible + .facet-sort-switch-track {
      outline: 2px solid #3b82f6;
      outline-offset: 2px;
    }
    body[data-theme="dark"] .facet-section-count {
      background: #334155;
      color: #e2e8f0;
    }
    body[data-theme="dark"] .facet-sort-option {
      color: #94a3b8;
    }
    body[data-theme="dark"] .facet-sort-option.active {
      color: #f1f5f9;
    }
    body[data-theme="dark"] .facet-sort-switch-track {
      background: #475569;
    }
    body[data-theme="dark"] .facet-sort-switch input:checked + .facet-sort-switch-track {
      background: #9b76c2;
    }
    .facet-section-list {
      display: flex;
      flex: 1 1 0;
      flex-direction: column;
      gap: 8px;
      min-height: 0;
      overflow: auto;
      padding: 8px;
    }
    .facet-group {
      display: flex;
      flex-direction: column;
      gap: 6px;
      min-width: 0;
    }
    .facet-header-row {
      display: flex;
      align-items: stretch;
      gap: 4px;
      min-width: 0;
    }
    .facet-header-row .facet-pill {
      flex: 1 1 auto;
      min-width: 0;
    }
    .facet-collapse-toggle {
      display: grid;
      flex: 0 0 34px;
      place-items: center;
      padding: 0;
      border: 1px solid #cfcfcf;
      border-radius: 8px;
      background: #fff;
      color: #444;
      cursor: pointer;
    }
    .facet-collapse-toggle:hover {
      background: #f2f4f6;
      border-color: #8a929b;
    }
    .facet-collapse-chevron {
      width: 7px;
      height: 7px;
      border-right: 2px solid currentColor;
      border-bottom: 2px solid currentColor;
      transform: rotate(-45deg);
      transition: transform 0.15s ease;
    }
    .facet-collapse-toggle[aria-expanded="true"] .facet-collapse-chevron {
      transform: rotate(45deg);
    }
    .facet-children {
      display: flex;
      flex-direction: column;
      gap: 6px;
      min-width: 0;
      margin-left: 12px;
      padding-left: 10px;
      border-left: 1px solid #d6dbe1;
    }
    #facetSidebar .tool-section + .tool-section {
      margin-top: 2px;
    }
    #facetSidebar .tool-section {
      margin: 0;
    }
    #timelineBlock {
      margin-top: 0 !important;
    }
    .facet-pill {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      width: 100%;
      max-width: 100%;
      box-sizing: border-box;
      border: 1px solid #cfcfcf;
      background: #fff;
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 12px;
      cursor: pointer;
      transition: transform 0.15s ease, background 0.15s ease, border-color 0.15s ease;
      text-align: left;
    }
    .facet-label {
      min-width: 0;
      overflow-wrap: anywhere;
    }
    .facet-pill.has-subsidiaries .facet-label {
      font-weight: 700;
    }
    .facet-pill:hover {
      background: #fff8e8;
      border-color: #ffb300;
      transform: translateY(-1px);
    }
    .facet-pill.selected {
      background: #fff2b3;
      border-color: #ffb300;
      font-weight: 700;
    }
    .facet-pill.keyword-facet {
      background: #dccbef;
      border-color: #7952a3;
      color: #30194b;
    }
    .facet-pill.keyword-facet:hover {
      background: #cdb5e6;
      border-color: #6b3fa0;
    }
    .facet-pill.keyword-facet.selected {
      background: #6b3fa0;
      border-color: #4b2677;
      color: #fff;
    }
    .keyword-facet-row {
      display: flex;
      align-items: center;
      gap: 6px;
      min-width: 0;
    }
    .keyword-facet-row .facet-pill {
      flex: 1 1 auto;
      min-width: 0;
    }
    .keyword-facet-row .facet-pill.inactive {
      opacity: 0.6;
    }
    .facet-pill:disabled {
      cursor: not-allowed;
    }
    .facet-active-toggle {
      display: flex;
      flex: 0 0 auto;
      align-items: center;
      cursor: pointer;
    }
    .facet-active-toggle input {
      margin: 0;
      accent-color: #7952a3;
    }
    .facet-pill .count {
      flex: 0 0 auto;
      color: #666;
      font-weight: 600;
    }
    .detail-panel {
      flex: 1 1 auto;
      min-height: 0;
      overflow: auto;
      border: 0;
      background: transparent;
      border-radius: 0;
      padding: 0;
      font-size: 12px;
      line-height: 1.45;
      color: #222;
      box-shadow: none;
      margin: 0;
    }
    .detail-panel.empty {
      color: #666;
      background: transparent;
      font-style: italic;
    }
    .detail-row {
      margin-bottom: 6px;
    }
    .detail-label {
      font-weight: 700;
      color: #333;
    }
    .detail-value {
      white-space: pre-wrap;
      word-break: break-word;
    }
    .detail-related {
      margin-top: 10px;
      padding-top: 10px;
      border-top: 1px solid #e7e7e7;
    }
    .detail-related-title {
      font-size: 12px;
      font-weight: 700;
      color: #444;
      margin-bottom: 6px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .detail-facet-list {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    .detail-hierarchy-path {
      align-items: center;
    }
    .detail-hierarchy-separator {
      color: #666;
      font-weight: 700;
    }
    .detail-facet-chip {
      appearance: none;
      border: 1px solid #d0d7e2;
      background: #f7f9fc;
      color: #17324d;
      border-radius: 999px;
      padding: 5px 10px;
      font-size: 12px;
      cursor: pointer;
      transition: all 0.15s ease;
    }
    .detail-facet-chip:hover {
      background: #e9f2ff;
      border-color: #9dc2ff;
    }
    .detail-facet-chip.selected {
      background: #FFE082;
      border-color: #FF8C00;
      color: #7a4a00;
      font-weight: 700;
    }
    .sidebar-item {
      width: 100%;
      text-align: left;
      background: #fff;
      border: 1px solid #e4e4e4;
      border-radius: 10px;
      padding: 8px 10px;
      cursor: pointer;
      transition: transform 0.15s ease, background 0.15s ease, border-color 0.15s ease;
      margin-bottom: 8px;
      box-sizing: border-box;
    }
    .sidebar-item:hover {
      background: #fff8e8;
      border-color: #ffb300;
      transform: translateX(2px);
    }
    .sidebar-item.selected {
      background: #fff2b3;
      border-color: #ffb300;
    }
    .related-match-group {
      margin-bottom: 8px;
      border: 1px solid #e4e4e4;
      border-radius: 8px;
      background: #fff;
    }
    .related-match-group > summary {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 8px;
      padding: 8px 10px;
      cursor: pointer;
      font-size: 12px;
      font-weight: 700;
      list-style-position: inside;
    }
    .related-match-group > summary:hover {
      background: #f5f7fa;
    }
    .related-match-count {
      flex: 0 0 auto;
      color: #666;
      font-weight: 600;
    }
    .related-match-items {
      padding: 0 8px 8px;
    }
    body[data-theme="dark"] .related-match-group {
      background: #111827;
      border-color: #334155;
    }
    body[data-theme="dark"] .related-match-group > summary:hover {
      background: #1e293b;
    }
    body[data-theme="dark"] .related-match-count {
      color: #cbd5e1;
    }
    .row-id {
      display: block;
      font-weight: 700;
      margin-bottom: 2px;
      color: #111;
    }
    .row-date {
      font-size: 12px;
      color: #333;
    }
    .row-box {
      font-size: 12px;
      color: #666;
      margin-top: 2px;
    }
    .row-match {
      font-size: 12px;
      color: #666;
      margin-top: 3px;
    }
    #emptyState {
      font-size: 13px;
      color: #666;
      padding: 10px 0;
    }
    #copyright {
      position: fixed;
      bottom: 10px;
      right: 12px;
      font-size: 12px;
      color: #666;
      background: rgba(255,255,255,0.8);
      padding: 4px 8px;
      border-radius: 6px;
      backdrop-filter: blur(2px);
      z-index: 9999;
    }
    .noUi-target { height: 5px; }
    .noUi-connect { background: #4C78A8; }
    .noUi-horizontal .noUi-handle {
      width: 12px;
      height: 12px;
      right: -6px;
      top: -4px;
      border-radius: 50%;
    }
    .noUi-handle:before,
    .noUi-handle:after { display: none; }
    body[data-theme="dark"] {
      background: #0f172a;
      color: #e5e7eb;
    }
    body[data-theme="dark"] #controls {
      background: #111827;
      border-bottom-color: #334155;
    }
    body[data-theme="dark"] #title,
    body[data-theme="dark"] #facetHeader,
    body[data-theme="dark"] #rowHeader,
    body[data-theme="dark"] #detailHeader,
    body[data-theme="dark"] #timelineHeader,
    body[data-theme="dark"] .detail-label,
    body[data-theme="dark"] .row-id,
    body[data-theme="dark"] .detail-related-title {
      color: #f8fafc;
    }
    body[data-theme="dark"] #rangeText,
    body[data-theme="dark"] #rowCount,
    body[data-theme="dark"] .row-date,
    body[data-theme="dark"] .row-box,
    body[data-theme="dark"] .row-match {
      color: #cbd5e1;
    }
    body[data-theme="dark"] #facetSidebar,
    body[data-theme="dark"] #dateSidebar,
    body[data-theme="dark"] #rowSidebar,
    body[data-theme="dark"] #detailSidebar {
      background: #0b1220;
      border-color: #334155;
    }
    body[data-theme="dark"] .facet-pill,
    body[data-theme="dark"] .sidebar-item,
    body[data-theme="dark"] .facet-collapse-toggle {
      background: #111827;
      border-color: #334155;
      color: #e5e7eb;
    }
    body[data-theme="dark"] .facet-pill:hover,
    body[data-theme="dark"] .sidebar-item:hover,
    body[data-theme="dark"] .facet-collapse-toggle:hover {
      background: #1e293b;
      border-color: #64748b;
    }
    body[data-theme="dark"] .facet-pill.selected,
    body[data-theme="dark"] .sidebar-item.selected {
      background: #334155;
      border-color: #60a5fa;
      color: #f8fafc;
    }
    body[data-theme="dark"] .facet-pill.keyword-facet {
      background: #3b2b50;
      border-color: #a884d2;
      color: #f2e8ff;
    }
    body[data-theme="dark"] .facet-pill.keyword-facet:hover {
      background: #4a3563;
      border-color: #c5a8ec;
    }
    body[data-theme="dark"] .facet-pill.keyword-facet.selected {
      background: #684395;
      border-color: #d5b8f4;
      color: #fff;
    }
    body[data-theme="dark"] .facet-pill .count {
      color: #94a3b8;
    }
    body[data-theme="dark"] .facet-children {
      border-color: #475569;
    }
    body[data-theme="dark"] .facet-section {
      border-color: #334155;
    }
    body[data-theme="dark"] .facet-section-header {
      border-bottom-color: #334155;
    }
    body[data-theme="dark"] .facet-section-title {
      color: #cbd5e1;
    }
    body[data-theme="dark"] .detail-panel.empty {
      background: transparent;
      color: #94a3b8;
    }
    body[data-theme="dark"] .detail-hierarchy-separator {
      color: #94a3b8;
    }
    body[data-theme="dark"] .detail-facet-chip {
      background: #1e293b;
      border-color: #475569;
      color: #e2e8f0;
    }
    body[data-theme="dark"] .detail-facet-chip:hover {
      background: #334155;
      border-color: #60a5fa;
    }
    body[data-theme="dark"] .detail-facet-chip.selected {
      background: #92400e;
      border-color: #f59e0b;
      color: #fef3c7;
    }
    body[data-theme="dark"] .facet-spotlight {
      background: #92400e;
      border-color: #f59e0b;
      color: #fef3c7;
    }
    body[data-theme="dark"] .toolbar-btn {
      background: #111827;
      border-color: #475569;
      color: #e5e7eb;
    }
    body[data-theme="dark"] .toolbar-btn:disabled {
      opacity: 0.5;
    }
    body[data-theme="dark"] #copyright {
      background: rgba(15, 23, 42, 0.88);
      color: #cbd5e1;
    }
    body[data-theme="dark"] .noUi-target {
      background: #334155;
      border-color: #475569;
    }
    body[data-theme="dark"] .noUi-connect {
      background: #60a5fa;
    }
    body[data-theme="dark"] .noUi-horizontal .noUi-handle {
      background: #e2e8f0;
      border-color: #cbd5e1;
      box-shadow: none;
    }
    body[data-theme="dark"] .noUi-handle:before,
    body[data-theme="dark"] .noUi-handle:after {
      background: #64748b;
    }
    body[data-theme="dark"] .detail-label,
    body[data-theme="dark"] .detail-value {
      color: #e5e7eb;
    }
    body[data-theme="dark"] .detail-value {
      white-space: pre-wrap;
    }
    @media (max-width: 950px) {
      #page {
        grid-template-columns: repeat(2, minmax(0, 1fr));
        grid-template-rows: minmax(130px, 150px) minmax(0, 1fr) minmax(240px, 34vh);
        grid-template-areas:
          "facets dates"
          "facets network"
          "related related";
        overflow: auto;
      }
      #rightSidebar {
        flex-direction: row;
      }
      #rowSidebar {
        min-height: 0;
      }
      #detailSidebar {
        flex: 0 0 38%;
        max-height: none;
        min-height: 0;
      }
    }
    @media (max-width: 700px) {
      #page {
        grid-template-columns: minmax(0, 1fr);
        grid-template-rows: minmax(190px, 38vh) minmax(145px, auto) minmax(380px, 60vh) minmax(430px, 65vh);
        grid-template-areas:
          "facets"
          "dates"
          "network"
          "related";
      }
      #facetSidebar {
        max-height: 58vh;
      }
      #rightSidebar {
        flex-direction: column;
      }
      #rowSidebar {
        min-height: 180px;
      }
      #detailSidebar {
        flex: 0 1 auto;
        min-height: 160px;
        max-height: 48%;
      }
    }
  </style>
</head>
<body>
  <div id="controls">
    <div id="title">__TITLE__</div>
    <div id="settingsMenu">
      <button type="button" id="settingsButton" class="toolbar-btn" aria-expanded="false" aria-controls="settingsPanel">Settings</button>
      <div id="settingsPanel" hidden>
        <label class="settings-label" for="themeToggle">
          <input type="checkbox" id="themeToggle">
          Dark mode
        </label>
        <label class="settings-label" for="keywordMinHits">
          Minimum keyword hits
          <input type="number" id="keywordMinHits" min="1" step="1" required>
        </label>
        <p id="keywordMinHitsHelp">A keyword appears in at least this many files.</p>
        <p id="keywordMinHitsError" role="status" aria-live="polite"></p>
      </div>
    </div>
    <div id="toolbar">
    </div>
  </div>

  <div id="page">
    <aside id="facetSidebar">
      <div class="panel-heading">
        <div id="facetHeader">Facet spotlight</div>
        <button id="clearFacets" class="toolbar-btn" disabled>Reset</button>
      </div>
      <div id="facetSections">
        <section class="facet-section" aria-labelledby="hierarchyFacetHeader">
          <div class="facet-section-header">
            <h3 id="hierarchyFacetHeader" class="facet-section-title">Finding aid hierarchy</h3>
          </div>
          <div id="hierarchyFacetList" class="facet-section-list"></div>
        </section>
        <section class="facet-section" aria-labelledby="keywordFacetHeader">
          <div class="facet-section-header">
            <h3 id="keywordFacetHeader" class="facet-section-title">
              Keyword facets <span id="keywordFacetCount" class="facet-section-count" aria-live="polite">0</span>
            </h3>
            <div class="facet-sort-control">
              <span id="keywordSortAlphabetical" class="facet-sort-option active">A-Z</span>
              <label class="facet-sort-switch">
                <input type="checkbox" id="keywordFacetSort" role="switch" aria-label="Sort keyword facets by number of connections">
                <span class="facet-sort-switch-track" aria-hidden="true"></span>
              </label>
              <span id="keywordSortConnections" class="facet-sort-option">Hi-Lo</span>
            </div>
          </div>
          <div id="keywordFacetList" class="facet-section-list"></div>
        </section>
      </div>
    </aside>

    <section id="dateSidebar" aria-labelledby="timelineHeader">
      <div id="timelineHeader">Date filtering</div>
      <div class="toggle-row" style="margin-top: 0;">
        <input type="checkbox" id="timelineToggle">
        <label for="timelineToggle">Enable timeline filter</label>
      </div>
      <div id="sliderWrap"><div id="slider"></div></div>
      <div id="rangeText">Timeline disabled - showing everything, including records without dates.</div>
    </section>

    <div id="container"></div>

    <aside id="rightSidebar">
      <section id="rowSidebar">
        <div id="rowHeader">Related files</div>
        <div id="rowCount"></div>
        <div id="rowList"></div>
        <div id="emptyState" style="display:none;">No rows match the current selection.</div>
      </section>

      <section id="detailSidebar">
        <div id="detailHeader">File spotlight</div>
        <div id="detailPanel" class="detail-panel empty">Hover or click a file node or row to preview details.</div>
      </section>
    </aside>
  </div>

  <script>
    const ROWS = __ROWS__;
    const FACETS = __FACETS__;
    const DEFAULT_KEYWORD_MIN_HITS = __DEFAULT_KEYWORD_MIN_HITS__;
    const FACETS_BY_ID = new Map(FACETS.map(facet => [facet.id, facet]));
    const availableDates = __DATES__;

    const container = document.getElementById('container');
    const slider = document.getElementById('slider');
    const rangeText = document.getElementById('rangeText');
    const timelineToggle = document.getElementById('timelineToggle');
    const clearFacets = document.getElementById('clearFacets');
    const themeToggle = document.getElementById('themeToggle');
    const settingsButton = document.getElementById('settingsButton');
    const settingsPanel = document.getElementById('settingsPanel');
    const keywordMinHitsInput = document.getElementById('keywordMinHits');
    const keywordMinHitsError = document.getElementById('keywordMinHitsError');
    const hierarchyFacetList = document.getElementById('hierarchyFacetList');
    const keywordFacetList = document.getElementById('keywordFacetList');
    const keywordFacetSort = document.getElementById('keywordFacetSort');
    const keywordFacetCount = document.getElementById('keywordFacetCount');
    const keywordSortAlphabetical = document.getElementById('keywordSortAlphabetical');
    const keywordSortConnections = document.getElementById('keywordSortConnections');
    const rowList = document.getElementById('rowList');
    const rowCount = document.getElementById('rowCount');
    const emptyState = document.getElementById('emptyState');
    const detailPanel = document.getElementById('detailPanel');

    const options = {
      interaction: { hover: true },
      nodes: { shape: 'dot', size: 14 },
      edges: {
        smooth: true,
        color: { color: '#999' },
        length: 110,
        width: 1.2
      },
      physics: {
        enabled: true,
        solver: 'forceAtlas2Based',
        forceAtlas2Based: {
          gravitationalConstant: -100,
          centralGravity: 0.01,
          springLength: 80,
          springConstant: 0.06,
          damping: 0.85,
          avoidOverlap: 1
        },
        stabilization: { iterations: 300, fit: true },
        maxVelocity: 12,
        minVelocity: 0.35,
        timestep: 0.35
      },
      groups: {
        row: {
          color: {
            background: '#4C78A8',
            border: '#4C78A8',
            highlight: { background: '#B7D8FF', border: '#2F6FDB' },
            hover: { background: '#6B95C8', border: '#4C78A8' }
          },
          font: { color: '#111111' },
          size: 18
        },
        facet: {
          color: {
            background: '#F58518',
            border: '#C96F10',
            highlight: '#FF9F1C',
            hover: '#FF9F1C'
          },
          font: { color: '#000000' }
        }
      }
    };

    const network = new vis.Network(container, { nodes: [], edges: [] }, options);

    let selectedFacetIds = new Set();
    let selectedRowId = null;
    let hoveredRowId = null;
    let rowHoverNetworkSelection = null;
    const THEME_KEY = 'rhizomatica-theme';
    const KEYWORD_MIN_HITS_KEY = `rhizomatica-keyword-min-hits:${window.location.pathname}`;
    const KEYWORD_INACTIVE_FACETS_KEY = `rhizomatica-keyword-inactive-facets:${window.location.pathname}`;
    let keywordMinHits = DEFAULT_KEYWORD_MIN_HITS;
    let inactiveKeywordFacetIds = new Set();
    try {
      const savedKeywordMinHits = Number(localStorage.getItem(KEYWORD_MIN_HITS_KEY));
      if (Number.isInteger(savedKeywordMinHits) && savedKeywordMinHits >= 1) {
        keywordMinHits = savedKeywordMinHits;
      }
    } catch (e) {
      console.warn('Unable to load the saved keyword minimum.', e);
    }
    try {
      const savedInactiveFacetIds = JSON.parse(localStorage.getItem(KEYWORD_INACTIVE_FACETS_KEY) || '[]');
      if (Array.isArray(savedInactiveFacetIds)) {
        inactiveKeywordFacetIds = new Set(
          savedInactiveFacetIds.filter(facetId =>
            typeof facetId === 'string' &&
            FACETS_BY_ID.get(facetId)?.kind === 'keyword'
          )
        );
      }
    } catch (e) {
      console.warn('Unable to load saved keyword facet states.', e);
    }
    keywordMinHitsInput.value = keywordMinHits;

    function getKeywordFacetsAboveThreshold() {
      return FACETS.filter(facet =>
        facet.kind === 'keyword' && facet.count >= keywordMinHits
      );
    }

    function getActiveFacets() {
      return FACETS.filter(facet =>
        facet.kind !== 'keyword' ||
        (facet.count >= keywordMinHits && !inactiveKeywordFacetIds.has(facet.id))
      );
    }
    const THEME_PALETTES = {
      light: {
        rowBackground: '#4C78A8',
        rowBorder: '#4C78A8',
        rowHoverBackground: '#6B95C8',
        rowSelectedBackground: '#6B95C8',
        rowSelectedBorder: '#2F6FDB',
        facetBackground: '#F58518',
        facetBorder: '#C96F10',
        facetHoverBackground: '#FF9F1C',
        facetSelectedBackground: '#FFE082',
        facetSelectedBorder: '#FF8C00',
        keywordFacetBackground: '#D8C4ED',
        keywordFacetBorder: '#7446A8',
        keywordFacetHoverBackground: '#C5A9E2',
        keywordFacetSelectedBackground: '#7446A8',
        keywordFacetSelectedBorder: '#512F7D',
        keywordFacetLabel: '#321A4A',
        keywordFacetSelectedLabel: '#FFFFFF',
        keywordFacetHighlight: 'rgba(116, 70, 168, 0.42)',
        rowLabel: '#111111',
        facetLabel: '#000000',
        edge: '#999',
        edgeSelected: '#FFB300',
        containmentEdge: '#52616B',
        detailHighlight: 'rgba(47, 111, 219, 0.28)',
        facetHighlight: 'rgba(255, 140, 0, 0.45)'
      },
      dark: {
        rowBackground: '#60A5FA',
        rowBorder: '#3B82F6',
        rowHoverBackground: '#93C5FD',
        rowSelectedBackground: '#2563EB',
        rowSelectedBorder: '#93C5FD',
        facetBackground: '#F59E0B',
        facetBorder: '#D97706',
        facetHoverBackground: '#FBBF24',
        facetSelectedBackground: '#92400E',
        facetSelectedBorder: '#FCD34D',
        keywordFacetBackground: '#4A3563',
        keywordFacetBorder: '#B99ADD',
        keywordFacetHoverBackground: '#5A4177',
        keywordFacetSelectedBackground: '#7653A6',
        keywordFacetSelectedBorder: '#DCC7F2',
        keywordFacetLabel: '#F4EAFE',
        keywordFacetSelectedLabel: '#FFFFFF',
        keywordFacetHighlight: 'rgba(185, 154, 221, 0.45)',
        rowLabel: '#F8FAFC',
        facetLabel: '#F8FAFC',
        edge: '#64748B',
        edgeSelected: '#F59E0B',
        containmentEdge: '#FACC15',
        detailHighlight: 'rgba(96, 165, 250, 0.32)',
        facetHighlight: 'rgba(251, 191, 36, 0.40)'
      }
    };
    let theme = 'light';

    function escapeHtml(value) {
      return String(value ?? '').replace(/[&<>'"]/g, ch => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        "'": '&#39;',
        '"': '&quot;'
      })[ch]);
    }

    function getTheme() {
      return THEME_PALETTES[theme] || THEME_PALETTES.light;
    }

    function applyTheme(nextTheme, persist = true) {
      theme = nextTheme === 'dark' ? 'dark' : 'light';
      document.body.dataset.theme = theme;
      if (themeToggle) themeToggle.checked = theme === 'dark';
      if (persist) {
        try {
          localStorage.setItem(THEME_KEY, theme);
        } catch (e) {
          /* ignore */
        }
      }
    }

    try {
      const savedTheme = localStorage.getItem(THEME_KEY);
      if (savedTheme) {
        theme = savedTheme;
      } else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
        theme = 'dark';
      }
    } catch (e) {
      /* ignore */
    }
    applyTheme(theme, false);

    if (availableDates.length >= 2) {
      noUiSlider.create(slider, {
        start: [0, availableDates.length - 1],
        connect: true,
        step: 1,
        range: { min: 0, max: availableDates.length - 1 }
      });
    } else {
      slider.style.display = 'none';
      timelineToggle.checked = false;
      timelineToggle.disabled = true;
      rangeText.textContent = 'No usable dates found - timeline filter disabled.';
    }

    function currentRange() {
      if (!slider.noUiSlider) return { startDate: null, endDate: null };
      const values = slider.noUiSlider.get().map(v => parseInt(v, 10));
      return {
        startDate: availableDates[values[0]],
        endDate: availableDates[values[1]]
      };
    }

    function rowMatchesTimeline(row, timelineOn, startDate, endDate) {
      if (!timelineOn) return true;
      if (!row.begin && !row.end) return false;
      return row.begin <= endDate && row.end >= startDate;
    }

    function rowMatchesFacets(row) {
      if (!selectedFacetIds.size) return true;
      for (const fid of selectedFacetIds) {
        if (!row.facets.includes(fid)) return false;
      }
      return true;
    }

    function getVisibleRows() {
      const timelineOn = timelineToggle.checked && !!slider.noUiSlider;
      const { startDate, endDate } = currentRange();
      return ROWS.filter(row => rowMatchesTimeline(row, timelineOn, startDate, endDate) && rowMatchesFacets(row));
    }

    function getTimelineRows() {
      const timelineOn = timelineToggle.checked && !!slider.noUiSlider;
      const { startDate, endDate } = currentRange();
      return ROWS.filter(row => rowMatchesTimeline(row, timelineOn, startDate, endDate));
    }

    function buildFacetCounts(rows) {
      const counts = new Map();
      FACETS.forEach(f => counts.set(f.id, 0));
      rows.forEach(row => {
        row.facets.forEach(fid => {
          if (counts.has(fid)) counts.set(fid, counts.get(fid) + 1);
        });
      });
      return counts;
    }

    function rowLabel(row) {
      const lines = [row.title || row.id];
      if (row.display_date) lines.push(row.display_date);
      return lines.join('\n');
    }

    // File nodes stay fixed and small; facet nodes have a floor well above
    // ROW_SIZE_HIGHLIGHTED so a facet is always bigger than any file node,
    // and grow from there with how many files are tagged to them.
    const ROW_SIZE = 9;
    const ROW_SIZE_HIGHLIGHTED = 13;
    const ROW_EXTENT_MAX_BOOST = 2;
    const ROW_FONT_SIZE = 10;
    const ROW_FONT_SIZE_HIGHLIGHTED = 12;
    let groupedRowsByNodeId = new Map();
    let hoveredGroupNodeId = null;
    const maxExtentValue = ROWS.reduce((maximum, row) => Math.max(maximum, row.extent_value || 0), 0);

    function rowNodeSize(row, highlighted) {
      const base = highlighted ? ROW_SIZE_HIGHLIGHTED : ROW_SIZE;
      if (!row.extent_value || maxExtentValue <= 0) return base;
      const extentGrowth = ROW_EXTENT_MAX_BOOST * Math.log1p(row.extent_value) / Math.log1p(maxExtentValue);
      return base + extentGrowth;
    }

    // The smallest-count facet always renders at FACET_MIN_SIZE and the
    // largest-count facet always renders at FACET_MAX_SIZE -- counts in
    // between are stretched across that full range (eased with
    // FACET_GROWTH_EXPONENT < 1 so growth is steep early then levels off).
    // Sizing this way means nothing saturates at the cap early and bumping
    // FACET_MAX_SIZE always visibly grows the biggest facets.
    const FACET_MIN_SIZE = 15;
    const FACET_MAX_SIZE = 150;
    const FACET_GROWTH_EXPONENT = 0.6;
    const FACET_SELECTED_BOOST = 14;
    const FACET_MIN_FONT_SIZE = 13;
    const FACET_MAX_FONT_SIZE = 40;

    const facetCountRange = FACETS.reduce(
      (range, f) => ({ min: Math.min(range.min, f.count), max: Math.max(range.max, f.count) }),
      { min: Infinity, max: -Infinity }
    );
    if (!Number.isFinite(facetCountRange.min)) {
      facetCountRange.min = 0;
      facetCountRange.max = 1;
    }

    function facetNodeSize(count, selected) {
      const span = facetCountRange.max - facetCountRange.min;
      const t = span > 0 ? (count - facetCountRange.min) / span : 1;
      const eased = Math.pow(Math.max(t, 0), FACET_GROWTH_EXPONENT);
      const base = FACET_MIN_SIZE + eased * (FACET_MAX_SIZE - FACET_MIN_SIZE);
      const boosted = selected ? base + FACET_SELECTED_BOOST : base;
      return Math.max(FACET_MIN_SIZE, Math.min(boosted, FACET_MAX_SIZE + FACET_SELECTED_BOOST));
    }

    function facetFontSize(size) {
      return Math.round(Math.max(FACET_MIN_FONT_SIZE, Math.min(size * 0.26, FACET_MAX_FONT_SIZE)));
    }

    function facetLabel(fid) {
      const facet = FACETS.find(f => f.id === fid);
      return facet ? facet.label : fid;
    }

    function buildFacetContainmentLinks(facets = FACETS) {
      const filesByFacet = new Map(facets.map(f => [f.id, new Set()]));
      ROWS.forEach(row => row.facets.forEach(fid => filesByFacet.get(fid)?.add(row.id)));
      const links = [];

      facets.forEach(child => {
        if (child.kind === 'hierarchy') {
          const parent = facets.find(f => f.id === child.parent_id && f.kind === 'hierarchy');
          if (parent) links.push({ from: child.id, to: parent.id });
          return;
        }

        const childFiles = filesByFacet.get(child.id);
        if (!childFiles || childFiles.size === 0) return;

        let smallestSupersetSize = Infinity;
        const parentIds = [];
        facets.forEach(parent => {
          if (parent.kind !== child.kind) return;
          const parentFiles = filesByFacet.get(parent.id);
          if (!parentFiles || parentFiles.size <= childFiles.size) return;
          if (![...childFiles].every(rowId => parentFiles.has(rowId))) return;

          if (parentFiles.size < smallestSupersetSize) {
            smallestSupersetSize = parentFiles.size;
            parentIds.length = 0;
          }
          if (parentFiles.size === smallestSupersetSize) parentIds.push(parent.id);
        });

        parentIds.forEach(parentId => links.push({ from: child.id, to: parentId }));
      });

      return links;
    }

    const FACET_CONTAINMENT_LINKS = buildFacetContainmentLinks();
    const HIERARCHICAL_FACETS = FACETS.filter(f => f.kind === 'hierarchy');
    const HIERARCHY_PARENTS_BY_CHILD = new Map(HIERARCHICAL_FACETS.map(f => [f.id, []]));
    const HIERARCHY_CHILDREN_BY_PARENT = new Map(HIERARCHICAL_FACETS.map(f => [f.id, []]));
    HIERARCHICAL_FACETS.forEach(facet => {
      const parentId = facet.parent_id;
      if (!parentId || !HIERARCHY_PARENTS_BY_CHILD.has(parentId)) return;
      HIERARCHY_PARENTS_BY_CHILD.get(facet.id).push(parentId);
      HIERARCHY_CHILDREN_BY_PARENT.get(parentId).push(facet.id);
    });
    const PRIMARY_HIERARCHY_PARENT_BY_ID = new Map();
    HIERARCHY_PARENTS_BY_CHILD.forEach((parentIds, childId) => {
      const [parentId] = parentIds;
      if (parentId) PRIMARY_HIERARCHY_PARENT_BY_ID.set(childId, parentId);
    });
    const expandedHierarchyFacetIds = new Set();

    function getHierarchyPath(facetId) {
      if (!HIERARCHY_PARENTS_BY_CHILD.has(facetId)) return [];

      const path = [facetId];
      const visited = new Set(path);
      let currentId = facetId;
      while (PRIMARY_HIERARCHY_PARENT_BY_ID.has(currentId)) {
        currentId = PRIMARY_HIERARCHY_PARENT_BY_ID.get(currentId);
        if (visited.has(currentId)) break;
        visited.add(currentId);
        path.push(currentId);
      }

      return path.reverse();
    }

    function expandHierarchyFacet(facetId) {
      getHierarchyPath(facetId).forEach(pathFacetId => {
        if ((HIERARCHY_CHILDREN_BY_PARENT.get(pathFacetId) || []).length) {
          expandedHierarchyFacetIds.add(pathFacetId);
        }
      });
    }

    function collapseHierarchyFacet(facetId) {
      const pathFacetIds = getHierarchyPath(facetId);
      const remainingSpotlightPathIds = new Set();
      selectedFacetIds.forEach(selectedId => {
        if (FACETS_BY_ID.get(selectedId)?.kind !== 'hierarchy') return;
        getHierarchyPath(selectedId).forEach(pathFacetId => remainingSpotlightPathIds.add(pathFacetId));
      });

      pathFacetIds.forEach(pathFacetId => {
        if (!remainingSpotlightPathIds.has(pathFacetId)) {
          expandedHierarchyFacetIds.delete(pathFacetId);
        }
      });
    }

    const FACET_PARENTS_BY_CHILD = new Map(FACETS.map(f => [f.id, []]));
    const FACET_CHILDREN_BY_PARENT = new Map(FACETS.map(f => [f.id, []]));
    FACET_CONTAINMENT_LINKS.forEach(link => FACET_PARENTS_BY_CHILD.get(link.from)?.push(link.to));
    FACET_CONTAINMENT_LINKS.forEach(link => FACET_CHILDREN_BY_PARENT.get(link.to)?.push(link.from));
    const PRIMARY_FACET_PARENT_BY_ID = new Map();
    FACET_PARENTS_BY_CHILD.forEach((parentIds, childId) => {
      const [primaryParentId] = parentIds.sort((a, b) => facetLabel(a).localeCompare(facetLabel(b)) || a.localeCompare(b));
      if (primaryParentId) PRIMARY_FACET_PARENT_BY_ID.set(childId, primaryParentId);
    });

    function getTopLevelFacetId(facetId) {
      let topLevelId = facetId;
      const visited = new Set();
      while (PRIMARY_FACET_PARENT_BY_ID.has(topLevelId) && !visited.has(topLevelId)) {
        visited.add(topLevelId);
        topLevelId = PRIMARY_FACET_PARENT_BY_ID.get(topLevelId);
      }
      return topLevelId;
    }

    const TOP_LEVEL_FACET_ID_BY_ID = new Map(FACETS.map(f => [f.id, getTopLevelFacetId(f.id)]));
    const COLLAPSIBLE_ROOT_FACET_IDS = new Set(
      FACETS
        .filter(f => TOP_LEVEL_FACET_ID_BY_ID.get(f.id) === f.id && (FACET_CHILDREN_BY_PARENT.get(f.id) || []).length > 0)
        .map(f => f.id)
    );
    const expandedTopLevelFacetIds = new Set();

    function isFacetBranchExpanded(facetId) {
      const rootId = TOP_LEVEL_FACET_ID_BY_ID.get(facetId);
      return !COLLAPSIBLE_ROOT_FACET_IDS.has(rootId) || expandedTopLevelFacetIds.has(rootId);
    }

    function isFacetNodeVisible(facetId) {
      return TOP_LEVEL_FACET_ID_BY_ID.get(facetId) === facetId || isFacetBranchExpanded(facetId);
    }

    function getRowsInExpandedBranches(rows) {
      const activeFacetIds = new Set(getActiveFacets().map(facet => facet.id));
      return rows.filter(row => {
        const rowFacetIds = row.facets.filter(facetId => activeFacetIds.has(facetId));
        if (!rowFacetIds.length) return true;
        return rowFacetIds.some(facetId => {
          const rootId = TOP_LEVEL_FACET_ID_BY_ID.get(facetId);
          return !COLLAPSIBLE_ROOT_FACET_IDS.has(rootId) || expandedTopLevelFacetIds.has(rootId);
        });
      });
    }

    function setFacetBranchExpanded(rootId, expanded) {
      if (!COLLAPSIBLE_ROOT_FACET_IDS.has(rootId)) return;

      if (expanded) {
        expandedTopLevelFacetIds.add(rootId);
        selectedFacetIds.add(rootId);
        return;
      }

      expandedTopLevelFacetIds.delete(rootId);
      for (const facetId of selectedFacetIds) {
        if (TOP_LEVEL_FACET_ID_BY_ID.get(facetId) === rootId) selectedFacetIds.delete(facetId);
      }
    }

    function toggleFacetSelection(facetId) {
      const rootId = TOP_LEVEL_FACET_ID_BY_ID.get(facetId);
      if (selectedFacetIds.has(facetId)) {
        if (facetId === rootId && COLLAPSIBLE_ROOT_FACET_IDS.has(rootId)) {
          setFacetBranchExpanded(rootId, false);
        } else {
          selectedFacetIds.delete(facetId);
          if (!Array.from(selectedFacetIds).some(id => TOP_LEVEL_FACET_ID_BY_ID.get(id) === rootId)) {
            expandedTopLevelFacetIds.delete(rootId);
          }
        }
        collapseHierarchyFacet(facetId);
        return;
      }

      selectedFacetIds.add(facetId);
      expandHierarchyFacet(facetId);
      if (COLLAPSIBLE_ROOT_FACET_IDS.has(rootId)) expandedTopLevelFacetIds.add(rootId);
    }

    function hasKeywordSpotlight() {
      return Array.from(selectedFacetIds).some(
        facetId => FACETS_BY_ID.get(facetId)?.kind === 'keyword'
      );
    }

    function hasHierarchySpotlight() {
      return Array.from(selectedFacetIds).some(
        facetId => FACETS_BY_ID.get(facetId)?.kind === 'hierarchy'
      );
    }

    function buildNetworkData(rows, matchingRows) {
      const visibleFacetIds = new Set();
      const activeFacetIds = new Set(getActiveFacets().map(facet => facet.id));
      const edges = [];
      const keywordSpotlighted = hasKeywordSpotlight();
      const selectedRow = ROWS.find(r => r.id === selectedRowId) || null;
      const hoveredRow = ROWS.find(r => r.id === hoveredRowId) || null;
      const networkRowsById = new Map(
        (keywordSpotlighted ? matchingRows : rows).map(row => [row.id, row])
      );
      if (!keywordSpotlighted) {
        matchingRows.forEach(row => {
          const hasVisibleKeywordFacet = row.facets.some(fid => {
            const facet = FACETS_BY_ID.get(fid);
            return facet?.kind === 'keyword' &&
              activeFacetIds.has(fid) &&
              isFacetNodeVisible(fid);
          });
          if (hasVisibleKeywordFacet) networkRowsById.set(row.id, row);
        });
      }
      const networkRows = Array.from(networkRowsById.values());

      matchingRows.forEach(row => row.facets.forEach(facetId => {
        const rootId = TOP_LEVEL_FACET_ID_BY_ID.get(facetId);
        if (COLLAPSIBLE_ROOT_FACET_IDS.has(rootId)) visibleFacetIds.add(rootId);
      }));

      networkRows.forEach(row => {
        row.facets.forEach(fid => {
          if (isFacetBranchExpanded(fid)) visibleFacetIds.add(fid);
          const facetKind = FACETS_BY_ID.get(fid)?.kind;
          if (keywordSpotlighted && (facetKind === 'hierarchy' || facetKind === 'keyword')) {
            visibleFacetIds.add(fid);
          }
        });
      });

      const facetNodes = [];
      getActiveFacets().forEach(f => {
        const supportsKeywordSpotlight = keywordSpotlighted && (
          (f.kind === 'hierarchy' && visibleFacetIds.has(f.id)) ||
          (f.kind === 'keyword' && visibleFacetIds.has(f.id))
        );
        if ((isFacetNodeVisible(f.id) || supportsKeywordSpotlight) && (visibleFacetIds.has(f.id) || selectedFacetIds.has(f.id))) {
          const isKeywordFacet = f.kind === 'keyword';
          const selected = selectedFacetIds.has(f.id);
          const size = facetNodeSize(f.count, selected);
          const background = isKeywordFacet ? getTheme().keywordFacetBackground : getTheme().facetBackground;
          const border = isKeywordFacet ? getTheme().keywordFacetBorder : getTheme().facetBorder;
          const hoverBackground = isKeywordFacet ? getTheme().keywordFacetHoverBackground : getTheme().facetHoverBackground;
          const selectedBackground = isKeywordFacet ? getTheme().keywordFacetSelectedBackground : getTheme().facetSelectedBackground;
          const selectedBorder = isKeywordFacet ? getTheme().keywordFacetSelectedBorder : getTheme().facetSelectedBorder;
          facetNodes.push({
            id: f.id,
            label: f.label,
            group: 'facet',
            title: `${escapeHtml(f.label)}<br>${f.count} row${f.count === 1 ? '' : 's'}`,
            size,
            font: {
              color: isKeywordFacet
                ? (selected ? getTheme().keywordFacetSelectedLabel : getTheme().keywordFacetLabel)
                : getTheme().facetLabel,
              size: facetFontSize(size),
              bold: selected
            },
            color: selected
              ? {
                  background: selectedBackground,
                  border: selectedBorder,
                  highlight: { background: selectedBackground, border: selectedBorder },
                  hover: { background: selectedBackground, border: selectedBorder }
                }
              : {
                  background,
                  border,
                  highlight: { background: hoverBackground, border },
                  hover: { background: hoverBackground, border }
                },
            borderWidth: selected ? 3 : 1.5,
            shadow: selected ? {
              enabled: true,
              color: isKeywordFacet ? getTheme().keywordFacetHighlight : getTheme().facetHighlight,
              size: 12,
              x: 0,
              y: 0
            } : false
          });
        }
      });

      const visibleFacetSet = new Set(facetNodes.map(n => n.id));
      const rowFacetTargets = new Map();
      const groupsByConnection = new Map();
      networkRows.forEach(row => {
        let targets;
        if (keywordSpotlighted) {
          const hierarchyFacets = row.facets.filter(fid => FACETS_BY_ID.get(fid)?.kind === 'hierarchy');
          const immediateHierarchyFacet = hierarchyFacets[hierarchyFacets.length - 1];
          const rowKeywordFacets = row.facets.filter(
            fid => FACETS_BY_ID.get(fid)?.kind === 'keyword'
          );
          targets = [immediateHierarchyFacet, ...rowKeywordFacets]
            .filter((fid, index, all) => fid && visibleFacetSet.has(fid) && all.indexOf(fid) === index);
        } else {
          const rowFacetIds = new Set(row.facets);
          targets = row.facets.filter(fid => {
            if (!isFacetNodeVisible(fid) || !visibleFacetSet.has(fid)) return false;
            const hasVisibleMoreSpecificRowFacet = (FACET_CHILDREN_BY_PARENT.get(fid) || [])
              .some(childId => rowFacetIds.has(childId) && activeFacetIds.has(childId) && isFacetNodeVisible(childId));
            return !hasVisibleMoreSpecificRowFacet;
          });
        }
        rowFacetTargets.set(row.id, targets);

        if (targets.length) {
          const connectionKey = JSON.stringify([...targets].sort());
          if (!groupsByConnection.has(connectionKey)) {
            groupsByConnection.set(connectionKey, { facetIds: [...targets].sort(), rows: [] });
          }
          groupsByConnection.get(connectionKey).rows.push(row);
        }
      });

      groupedRowsByNodeId = new Map();
      const groupedRowIds = new Set();
      const rowIds = new Set(ROWS.map(row => row.id));
      const fileNodes = [];
      let groupIndex = 0;
      groupsByConnection.forEach(group => {
        if (group.rows.length < 2 || group.facetIds.some(fid => selectedFacetIds.has(fid))) return;
        let groupId;
        do {
          groupId = `__rhizomatica_file_group_${groupIndex++}`;
        } while (rowIds.has(groupId));

        groupedRowsByNodeId.set(groupId, group);
        group.rows.forEach(row => groupedRowIds.add(row.id));
        fileNodes.push({
          id: groupId,
          label: `${group.rows.length} files within`,
          group: 'row',
          size: ROW_SIZE * Math.sqrt(group.rows.length),
          font: { color: getTheme().rowLabel, size: ROW_FONT_SIZE, bold: true },
          borderWidth: 1.5,
          color: {
            background: getTheme().rowBackground,
            border: getTheme().rowBorder,
            highlight: { background: getTheme().rowHoverBackground, border: getTheme().rowBorder },
            hover: { background: getTheme().rowHoverBackground, border: getTheme().rowBorder }
          },
          title: `<b>${group.rows.length} files within</b><br>Connected facets: ${group.facetIds.map(facetLabel).map(escapeHtml).join(', ')}`
        });

        group.facetIds.forEach(fid => {
          edges.push({
            from: groupId,
            to: fid,
            width: selectedFacetIds.has(fid) ? 3 : 1.2,
            color: selectedFacetIds.has(fid) ? { color: getTheme().edgeSelected } : { color: getTheme().edge },
            title: `Connected facet: ${fid}`
          });
        });
      });

      networkRows.forEach(row => {
        if (groupedRowIds.has(row.id)) return;
        const highlighted = (selectedRow && selectedRow.id === row.id) || (hoveredRow && hoveredRow.id === row.id);
        fileNodes.push({
          id: row.id,
          label: rowLabel(row),
          group: 'row',
          size: rowNodeSize(row, highlighted),
          font: {
            color: getTheme().rowLabel,
            size: highlighted ? ROW_FONT_SIZE_HIGHLIGHTED : ROW_FONT_SIZE,
            bold: highlighted
          },
          borderWidth: highlighted ? 3 : 1.5,
          color: highlighted
            ? {
                background: getTheme().rowSelectedBackground,
                border: getTheme().rowSelectedBorder,
                highlight: { background: getTheme().rowSelectedBackground, border: getTheme().rowSelectedBorder },
                hover: { background: getTheme().rowHoverBackground, border: getTheme().rowSelectedBorder }
              }
            : {
                background: getTheme().rowBackground,
                border: getTheme().rowBorder,
                highlight: { background: getTheme().rowHoverBackground, border: getTheme().rowSelectedBorder },
                hover: { background: getTheme().rowHoverBackground, border: getTheme().rowBorder }
              },
          shadow: highlighted ? {
            enabled: true,
            color: getTheme().detailHighlight,
            size: 10,
            x: 0,
            y: 0
          } : false,
          title: [
            `<b>Item name:</b> ${escapeHtml(row.title || row.id)}`,
            row.display_date ? `<b>Dates:</b> ${escapeHtml(row.display_date)}` : '<b>Dates:</b> undated',
            row.scope_content ? `<b>Scope content:</b> ${escapeHtml(row.scope_content)}` : '',
            row.physical_desc ? `<b>Physical desc:</b> ${escapeHtml(row.physical_desc)}` : ''
          ].filter(Boolean).join('<br>')
        });
      });

      const nodes = fileNodes.concat(facetNodes);
      networkRows.forEach(row => {
        if (groupedRowIds.has(row.id)) return;
        (rowFacetTargets.get(row.id) || []).forEach(fid => {
          edges.push({
            from: row.id,
            to: fid,
            width: selectedFacetIds.has(fid) ? 3 : 1.2,
            color: selectedFacetIds.has(fid) ? { color: getTheme().edgeSelected } : { color: getTheme().edge },
            title: `Connected facet: ${fid}`
          });
        });
      });

      const containmentLinks = keywordSpotlighted
        ? HIERARCHICAL_FACETS
          .map(facet => ({ from: facet.id, to: PRIMARY_HIERARCHY_PARENT_BY_ID.get(facet.id) }))
          .filter(link => link.to)
        : FACET_CONTAINMENT_LINKS;
      containmentLinks.forEach(link => {
        if (!visibleFacetSet.has(link.from) || !visibleFacetSet.has(link.to)) return;
        const child = FACETS_BY_ID.get(link.from);
        const parent = FACETS_BY_ID.get(link.to);
        if (!child || !parent) return;

        edges.push({
          from: link.from,
          to: link.to,
          arrows: { to: { enabled: true, scaleFactor: 0.6 } },
          dashes: [8, 5],
          width: 2.2,
          color: { color: getTheme().containmentEdge, opacity: 0.8 },
          title: `<b>${keywordSpotlighted ? 'Hierarchy containment' : 'Facet subset'}</b><br>Contained by: ${escapeHtml(parent.label)}<br>${child.count} files`
        });
      });

      getActiveFacets().filter(facet => facet.kind === 'keyword').forEach(keywordFacet => {
        (keywordFacet.matched_hierarchy_ids || []).forEach(hierarchyId => {
          if (!visibleFacetSet.has(keywordFacet.id) || !visibleFacetSet.has(hierarchyId)) return;
          const hierarchyFacet = FACETS_BY_ID.get(hierarchyId);
          if (!hierarchyFacet) return;

          edges.push({
            from: keywordFacet.id,
            to: hierarchyId,
            arrows: { to: { enabled: true, scaleFactor: 0.6 } },
            dashes: [4, 4],
            width: 2,
            color: { color: getTheme().keywordFacetBorder, opacity: 0.85 },
            title: `<b>Keyword match</b><br>${escapeHtml(keywordFacet.label)} appears in ${escapeHtml(hierarchyFacet.label)}`
          });
        });
      });

      return { nodes, edges };
    }

    function getCurrentDetailRow() {
      return ROWS.find(r => r.id === selectedRowId) || ROWS.find(r => r.id === hoveredRowId) || null;
    }

    function renderDetailPanel() {
      const groupedNode = selectedRowId ? null : groupedRowsByNodeId.get(hoveredGroupNodeId);
      if (groupedNode) {
        detailPanel.classList.remove('empty');
        detailPanel.innerHTML = `
          <div class="detail-row"><span class="detail-label">Files within:</span> <span class="detail-value">${groupedNode.rows.length}</span></div>
          <div class="detail-row"><span class="detail-label">Connected facets:</span> <span class="detail-value">${groupedNode.facetIds.map(facetLabel).map(escapeHtml).join(', ')}</span></div>
        `;
        return;
      }

      const row = getCurrentDetailRow();
      if (!row) {
        detailPanel.classList.add('empty');
        detailPanel.innerHTML = 'Hover or click a file node or row to preview details.';
        return;
      }

      const relatedHierarchy = (row.facets || [])
        .filter(fid => FACETS_BY_ID.get(fid)?.kind === 'hierarchy')
        .map(fid => ({ id: fid, label: facetLabel(fid) }));
      const activeKeywordFacetIds = new Set(
        getActiveFacets()
          .filter(facet => facet.kind === 'keyword')
          .map(facet => facet.id)
      );
      const relatedKeywords = (row.facets || [])
        .filter(fid => activeKeywordFacetIds.has(fid))
        .map(fid => ({ id: fid, label: facetLabel(fid) }));

      detailPanel.classList.remove('empty');
      detailPanel.innerHTML = `
        <div class="detail-row"><span class="detail-label">Item name:</span> <span class="detail-value">${escapeHtml(row.title || row.id)}</span></div>
        <div class="detail-row"><span class="detail-label">Dates:</span> <span class="detail-value">${escapeHtml(row.display_date || 'undated')}</span></div>
        <div class="detail-row"><span class="detail-label">Container instance:</span> <span class="detail-value">${escapeHtml(row.container_instance || '—')}</span></div>
        ${(row.extents || []).length ? `<div class="detail-row"><span class="detail-label">Extents:</span> <span class="detail-value">${escapeHtml(row.extents.join('; '))}</span></div>` : ''}
        ${row.general_note ? `<div class="detail-row"><span class="detail-label">General note:</span> <span class="detail-value">${escapeHtml(row.general_note)}</span></div>` : ''}
        ${row.scope_content ? `<div class="detail-row"><span class="detail-label">Scope and content:</span> <span class="detail-value">${escapeHtml(row.scope_content)}</span></div>` : ''}
        <div class="detail-related">
          <div class="detail-related-title">Hierarchy</div>
          ${relatedHierarchy.length ? `<div class="detail-facet-list detail-hierarchy-path">${relatedHierarchy.map((f, index) => `${index ? '<span class="detail-hierarchy-separator" aria-hidden="true">&gt;</span>' : ''}<button type="button" class="detail-facet-chip ${selectedFacetIds.has(f.id) ? 'selected' : ''}" data-related-facet="${escapeHtml(f.id)}">${escapeHtml(f.label)}</button>`).join('')}</div>` : '<div class="detail-row"><span class="detail-value">No hierarchy assigned.</span></div>'}
          <div class="detail-related-title">Keyword facets</div>
          ${relatedKeywords.length ? `<div class="detail-facet-list">${relatedKeywords.map(f => `<button type="button" class="detail-facet-chip ${selectedFacetIds.has(f.id) ? 'selected' : ''}" data-related-facet="${escapeHtml(f.id)}">${escapeHtml(f.label)}</button>`).join('')}</div>` : '<div class="detail-row"><span class="detail-value">No keyword facets.</span></div>'}
        </div>
      `;

      detailPanel.querySelectorAll('[data-related-facet]').forEach(btn => {
        btn.addEventListener('click', () => {
          const fid = btn.dataset.relatedFacet;
          hoveredGroupNodeId = null;
          toggleFacetSelection(fid);
          render();
        });
      });
    }

    function networkNodeIdForRow(rowId) {
      for (const [groupId, group] of groupedRowsByNodeId) {
        if (group.rows.some(row => row.id === rowId)) return groupId;
      }
      return rowId;
    }

    function spotlightNetworkRow(rowId) {
      if (rowHoverNetworkSelection === null) {
        rowHoverNetworkSelection = network.getSelectedNodes();
      }
      network.selectNodes([networkNodeIdForRow(rowId)]);
    }

    function clearNetworkRowSpotlight() {
      if (rowHoverNetworkSelection === null) return;
      network.selectNodes(rowHoverNetworkSelection);
      rowHoverNetworkSelection = null;
    }

    function selectSpotlightRow(rowId, focus = false) {
      clearNetworkRowSpotlight();
      hoveredGroupNodeId = null;
      selectedRowId = rowId;
      hoveredRowId = rowId;

      const nodeId = networkNodeIdForRow(rowId);
      network.selectNodes([nodeId]);
      if (focus) network.focus(nodeId, { scale: 1.2, animation: true });

      rowList.querySelectorAll('[data-row-id]').forEach(btn => {
        btn.classList.toggle('selected', btn.dataset.rowId === rowId);
      });
      renderDetailPanel();
    }

    function renderFacetList(rows) {
      const counts = buildFacetCounts(rows);
      const keywordSpotlighted = hasKeywordSpotlight();
      const hasMatchingFiles = facet => selectedFacetIds.has(facet.id) || (counts.get(facet.id) || 0) > 0;
      const childrenByParent = HIERARCHY_CHILDREN_BY_PARENT;
      const rootFacets = [];

      HIERARCHICAL_FACETS.forEach(facet => {
        if (!PRIMARY_HIERARCHY_PARENT_BY_ID.has(facet.id) && hasMatchingFiles(facet)) rootFacets.push(facet);
      });

      function renderFacet(facet) {
        const selected = selectedFacetIds.has(facet.id);
        const children = (childrenByParent.get(facet.id) || [])
          .map(facetId => FACETS.find(candidate => candidate.id === facetId))
          .filter(child => child && hasMatchingFiles(child));
        const canCollapse = children.length > 0;
        const isExpanded = expandedHierarchyFacetIds.has(facet.id);
        const alternateParents = (HIERARCHY_PARENTS_BY_CHILD.get(facet.id) || [])
          .filter(parentId => parentId !== PRIMARY_HIERARCHY_PARENT_BY_ID.get(facet.id))
          .map(facetLabel);
        const primaryParentId = PRIMARY_HIERARCHY_PARENT_BY_ID.get(facet.id);
        const tooltip = primaryParentId
          ? `Contained by ${facetLabel(primaryParentId)}${alternateParents.length ? `; also contained by ${alternateParents.join(', ')}` : ''}`
          : '';
        const classes = ['facet-pill'];
        if (facet.kind === 'keyword') classes.push('keyword-facet');
        if (selected) classes.push('selected');
        if (children.length) classes.push('has-subsidiaries');

        return `
          <div class="facet-group">
            <div class="facet-header-row">
              <button type="button" class="${classes.join(' ')}" data-facet-id="${facet.id}"${tooltip ? ` title="${escapeHtml(tooltip)}"` : ''}>
                <span class="facet-label">${escapeHtml(facet.label)}</span>
                <span class="count">${counts.get(facet.id) || 0}</span>
              </button>
              ${canCollapse ? `<button type="button" class="facet-collapse-toggle" data-collapse-facet="${escapeHtml(facet.id)}" aria-label="${isExpanded ? 'Collapse' : 'Expand'} ${escapeHtml(facet.label)}" aria-expanded="${isExpanded}" title="${isExpanded ? 'Collapse' : 'Expand'} subsidiary facets"><span class="facet-collapse-chevron" aria-hidden="true"></span></button>` : ''}
            </div>
            ${canCollapse && isExpanded ? `<div class="facet-children">${children.map(child => renderFacet(child)).join('')}</div>` : ''}
          </div>
        `;
      }

      const facetButton = facet => {
        const count = counts.get(facet.id) || 0;
        const countLabel = keywordSpotlighted ? `${count} (of ${facet.count})` : count;
        const active = !inactiveKeywordFacetIds.has(facet.id);
        return `
          <div class="keyword-facet-row">
            <button type="button" class="facet-pill keyword-facet ${active ? '' : 'inactive'} ${selectedFacetIds.has(facet.id) ? 'selected' : ''}" data-facet-id="${escapeHtml(facet.id)}" ${active ? '' : 'disabled'}>
              <span class="facet-label">${escapeHtml(facet.label)}</span>
              <span class="count">${countLabel}</span>
            </button>
            <label class="facet-active-toggle" title="Include this keyword in the network and file spotlights">
              <input type="checkbox" role="switch" data-keyword-active-id="${escapeHtml(facet.id)}" aria-label="Show ${escapeHtml(facet.label)} in the network and file spotlights" ${active ? 'checked' : ''}>
            </label>
          </div>
        `;
      };

      hierarchyFacetList.innerHTML = rootFacets.map(facet => renderFacet(facet)).join('');
      const keywordFacets = getKeywordFacetsAboveThreshold()
        .filter(facet => hasMatchingFiles(facet))
        .sort((a, b) => {
          const alphabeticalOrder = a.label.localeCompare(b.label);
          if (keywordFacetSort.checked) {
            return (counts.get(b.id) || 0) - (counts.get(a.id) || 0) || alphabeticalOrder;
          }
          return alphabeticalOrder;
        });
      keywordFacetCount.textContent = keywordFacets.length;
      keywordSortAlphabetical.classList.toggle('active', !keywordFacetSort.checked);
      keywordSortConnections.classList.toggle('active', keywordFacetSort.checked);
      keywordFacetList.innerHTML = keywordFacets.length
        ? keywordFacets.map(facet => facetButton(facet)).join('')
        : '<div class="detail-panel empty">No keyword facets match the current spotlight.</div>';

      [hierarchyFacetList, keywordFacetList].forEach(list => {
        list.querySelectorAll('[data-facet-id]').forEach(btn => {
          btn.addEventListener('click', () => {
            const fid = btn.dataset.facetId;
            toggleFacetSelection(fid);
            selectedRowId = null;
            hoveredRowId = null;
            render();
          });
        });
      });

      hierarchyFacetList.querySelectorAll('[data-collapse-facet]').forEach(btn => {
        btn.addEventListener('click', () => {
          const facetId = btn.dataset.collapseFacet;
          if (expandedHierarchyFacetIds.has(facetId)) {
            expandedHierarchyFacetIds.delete(facetId);
          } else {
            expandedHierarchyFacetIds.add(facetId);
          }
          renderFacetList(getVisibleRows());
        });
      });

      keywordFacetList.querySelectorAll('[data-keyword-active-id]').forEach(input => {
        input.addEventListener('change', () => {
          const facetId = input.dataset.keywordActiveId;
          if (input.checked) {
            inactiveKeywordFacetIds.delete(facetId);
          } else {
            inactiveKeywordFacetIds.add(facetId);
            selectedFacetIds.delete(facetId);
          }
          try {
            localStorage.setItem(
              KEYWORD_INACTIVE_FACETS_KEY,
              JSON.stringify(Array.from(inactiveKeywordFacetIds))
            );
          } catch (e) {
            console.warn('Unable to save keyword facet states.', e);
          }
          render();
        });
      });

      clearFacets.disabled = selectedFacetIds.size === 0;
    }

    function renderRowList(rows, matchingRowCount = rows.length) {
      const hiddenRowCount = matchingRowCount - rows.length;
      rowCount.textContent = hiddenRowCount > 0
        ? `${rows.length} shown, ${hiddenRowCount} hidden in collapsed facets`
        : `${rows.length} matching row${rows.length === 1 ? '' : 's'}`;
      emptyState.style.display = rows.length ? 'none' : 'block';
      emptyState.textContent = matchingRowCount
        ? 'Matching files are hidden under collapsed facets.'
        : 'No rows match the current selection.';

      const renderRow = row => {
        const selected = row.id === selectedRowId;
        const matched = row.facets.filter(fid => selectedFacetIds.has(fid)).join(', ');
        return `
          <button type="button" class="sidebar-item ${selected ? 'selected' : ''}" data-row-id="${row.id}">
            <span class="row-id">${escapeHtml(row.title || row.id)}</span>
            <div class="row-date">${escapeHtml(row.display_date || 'undated')}</div>
            ${row.box ? `<div class="row-box">${escapeHtml(row.box)}</div>` : ''}
            ${matched ? `<div class="row-match">Matches: ${escapeHtml(matched)}</div>` : ''}
          </button>
        `;
      };

      if (hasKeywordSpotlight() && rows.length) {
        const selectedKeywordFacets = Array.from(selectedFacetIds)
          .map(facetId => FACETS_BY_ID.get(facetId))
          .filter(facet => facet?.kind === 'keyword');
        const fileLevelRowIds = new Set(
          selectedKeywordFacets.flatMap(facet => facet.direct_file_row_ids || [])
        );
        selectedKeywordFacets.forEach(facet => {
          if ((facet.matched_hierarchy_ids || []).length) return;
          rows.forEach(row => {
            if (row.facets.includes(facet.id)) fileLevelRowIds.add(row.id);
          });
        });

        const hierarchyMatchesByRowId = new Map();
        selectedKeywordFacets.forEach(facet => {
          (facet.matched_hierarchy_ids || []).forEach(hierarchyId => {
            rows.forEach(row => {
              if (
                !fileLevelRowIds.has(row.id) &&
                row.facets.includes(facet.id) &&
                row.facets.includes(hierarchyId)
              ) {
                if (!hierarchyMatchesByRowId.has(row.id)) {
                  hierarchyMatchesByRowId.set(row.id, new Set());
                }
                hierarchyMatchesByRowId.get(row.id).add(hierarchyId);
              }
            });
          });
        });

        const hierarchyRows = new Map();
        const fileLevelRows = [];
        rows.forEach(row => {
          const hierarchyIds = Array.from(hierarchyMatchesByRowId.get(row.id) || []);
          if (!hierarchyIds.length) {
            fileLevelRows.push(row);
            return;
          }

          hierarchyIds.sort((a, b) => getHierarchyPath(b).length - getHierarchyPath(a).length);
          const hierarchyId = hierarchyIds[0];
          if (!hierarchyRows.has(hierarchyId)) hierarchyRows.set(hierarchyId, []);
          hierarchyRows.get(hierarchyId).push(row);
        });

        const hierarchyGroups = Array.from(hierarchyRows, ([hierarchyId, groupRows]) => ({
          hierarchyId,
          rows: groupRows,
          path: getHierarchyPath(hierarchyId).map(facetLabel).join(' > ')
        })).sort((a, b) => a.path.localeCompare(b.path));
        rowList.innerHTML = `
          <details class="related-match-group" open>
            <summary><span>File-level matches</span><span class="related-match-count">${fileLevelRows.length}</span></summary>
            <div class="related-match-items">${fileLevelRows.length
              ? fileLevelRows.map(renderRow).join('')
              : '<div class="detail-panel empty">No file-level matches.</div>'}</div>
          </details>
          ${hierarchyGroups.map(group => `
            <details class="related-match-group">
              <summary><span>${escapeHtml(group.path)}</span><span class="related-match-count">${group.rows.length}</span></summary>
              <div class="related-match-items">${group.rows.map(renderRow).join('')}</div>
            </details>
          `).join('')}
        `;
      } else if (hasHierarchySpotlight() && rows.length) {
        const selectedHierarchyFacets = Array.from(selectedFacetIds)
          .map(facetId => FACETS_BY_ID.get(facetId))
          .filter(facet => facet?.kind === 'hierarchy');
        const hierarchyRows = new Map();
        rows.forEach(row => {
          const matchingHierarchyFacets = selectedHierarchyFacets
            .filter(facet => row.facets.includes(facet.id))
            .sort((a, b) => getHierarchyPath(b.id).length - getHierarchyPath(a.id).length);
          const hierarchyFacet = matchingHierarchyFacets[0];
          if (!hierarchyFacet) return;
          if (!hierarchyRows.has(hierarchyFacet.id)) hierarchyRows.set(hierarchyFacet.id, []);
          hierarchyRows.get(hierarchyFacet.id).push(row);
        });

        const hierarchyGroups = Array.from(hierarchyRows, ([hierarchyId, groupRows]) => ({
          hierarchyId,
          rows: groupRows,
          path: getHierarchyPath(hierarchyId).map(facetLabel).join(' > ')
        })).sort((a, b) => a.path.localeCompare(b.path));
        rowList.innerHTML = hierarchyGroups.map(group => `
          <details class="related-match-group" open>
            <summary><span>${escapeHtml(group.path)}</span><span class="related-match-count">${group.rows.length}</span></summary>
            <div class="related-match-items">${group.rows.map(renderRow).join('')}</div>
          </details>
        `).join('');
      } else {
        rowList.innerHTML = rows.map(renderRow).join('');
      }

      rowList.querySelectorAll('[data-row-id]').forEach(btn => {
        const rowId = btn.dataset.rowId;
        btn.addEventListener('mouseenter', () => {
          hoveredGroupNodeId = null;
          hoveredRowId = rowId;
          spotlightNetworkRow(rowId);
          renderDetailPanel();
        });
        btn.addEventListener('click', () => {
          selectSpotlightRow(rowId, true);
        });
      });
    }

    function render() {
      applyTheme(theme, false);
      const timelineOn = timelineToggle.checked && !!slider.noUiSlider;
      const { startDate, endDate } = currentRange();
      const timelineRows = getTimelineRows();
      const matchingRows = getVisibleRows();
      const visibleRows = getRowsInExpandedBranches(matchingRows);

      if (selectedRowId && !matchingRows.some(r => r.id === selectedRowId)) selectedRowId = null;
      if (hoveredRowId && !matchingRows.some(r => r.id === hoveredRowId)) hoveredRowId = null;

      renderFacetList(matchingRows);
      renderRowList(hasKeywordSpotlight() ? matchingRows : visibleRows, matchingRows.length);

      const data = buildNetworkData(visibleRows, matchingRows);
      if (!groupedRowsByNodeId.has(hoveredGroupNodeId)) hoveredGroupNodeId = null;
      clearNetworkRowSpotlight();
      network.setData({
        nodes: new vis.DataSet(data.nodes),
        edges: new vis.DataSet(data.edges)
      });
      renderDetailPanel();

      if (!timelineOn) {
        rangeText.innerHTML = selectedFacetIds.size
          ? `Timeline disabled - spotlighting <span class="facet-spotlight">${Array.from(selectedFacetIds).join(', ')}</span>.`
          : 'Timeline disabled - showing everything, including records without dates.';
      } else {
        rangeText.innerHTML = selectedFacetIds.size
          ? `Timeline enabled - ${startDate} to ${endDate} - spotlighting <span class="facet-spotlight">${Array.from(selectedFacetIds).join(', ')}</span>.`
          : `Timeline enabled - showing records active from ${startDate} to ${endDate}.`;
      }
    }

    network.on('click', function(params) {
      if (params.nodes.length === 1) {
        const clickedId = params.nodes[0];
        if (groupedRowsByNodeId.has(clickedId)) {
          const groupedNode = groupedRowsByNodeId.get(clickedId);
          selectedFacetIds.clear();
          expandedTopLevelFacetIds.clear();
          expandedHierarchyFacetIds.clear();
          groupedNode.facetIds.forEach(facetId => {
            selectedFacetIds.add(facetId);
            expandHierarchyFacet(facetId);
            const rootId = TOP_LEVEL_FACET_ID_BY_ID.get(facetId);
            if (COLLAPSIBLE_ROOT_FACET_IDS.has(rootId)) expandedTopLevelFacetIds.add(rootId);
          });
          selectedRowId = null;
          hoveredRowId = null;
          hoveredGroupNodeId = clickedId;
          render();
          return;
        }
        const isFacet = getActiveFacets().some(f => f.id === clickedId);
        if (isFacet) {
          toggleFacetSelection(clickedId);
          selectedRowId = null;
          hoveredRowId = null;
          hoveredGroupNodeId = null;
          render();
          return;
        }

        selectSpotlightRow(clickedId);
        return;
      }

      if (params.nodes.length === 0 && params.edges.length === 0) {
        if (!selectedFacetIds.size && !expandedTopLevelFacetIds.size && !expandedHierarchyFacetIds.size) return;
        selectedFacetIds.clear();
        expandedTopLevelFacetIds.clear();
        expandedHierarchyFacetIds.clear();
        selectedRowId = null;
        hoveredRowId = null;
        hoveredGroupNodeId = null;
        render();
      }
    });

    network.on('hoverNode', function(params) {
      const hoveredId = params.node;
      const isFacet = getActiveFacets().some(f => f.id === hoveredId);
      if (groupedRowsByNodeId.has(hoveredId)) {
        hoveredGroupNodeId = hoveredId;
        hoveredRowId = null;
        renderDetailPanel();
      } else if (!isFacet) {
        hoveredGroupNodeId = null;
        hoveredRowId = hoveredId;
        renderDetailPanel();
      }
    });

    network.on('blurNode', function(params) {
      const blurredId = params.node;
      if (hoveredGroupNodeId === blurredId) {
        hoveredGroupNodeId = null;
        renderDetailPanel();
        return;
      }
      if (hoveredRowId === blurredId && selectedRowId !== blurredId) {
        hoveredRowId = null;
        renderDetailPanel();
      }
    });

    if (themeToggle) {
      themeToggle.addEventListener('change', function() {
        applyTheme(this.checked ? 'dark' : 'light');
        render();
      });
    }

    settingsButton.addEventListener('click', () => {
      const isExpanded = settingsButton.getAttribute('aria-expanded') === 'true';
      settingsButton.setAttribute('aria-expanded', String(!isExpanded));
      settingsPanel.hidden = isExpanded;
    });

    document.addEventListener('click', event => {
      if (event.target instanceof Node && !settingsPanel.contains(event.target) && event.target !== settingsButton) {
        settingsPanel.hidden = true;
        settingsButton.setAttribute('aria-expanded', 'false');
      }
    });

    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && !settingsPanel.hidden) {
        settingsPanel.hidden = true;
        settingsButton.setAttribute('aria-expanded', 'false');
        settingsButton.focus();
      }
    });

    keywordMinHitsInput.addEventListener('change', () => {
      const nextKeywordMinHits = Number(keywordMinHitsInput.value);
      if (!Number.isSafeInteger(nextKeywordMinHits) || nextKeywordMinHits < 1) {
        keywordMinHitsInput.setAttribute('aria-invalid', 'true');
        keywordMinHitsError.textContent = 'Enter a whole number greater than 0.';
        return;
      }

      keywordMinHits = nextKeywordMinHits;
      keywordMinHitsInput.removeAttribute('aria-invalid');
      keywordMinHitsError.textContent = '';
      try {
        localStorage.setItem(KEYWORD_MIN_HITS_KEY, String(keywordMinHits));
      } catch (e) {
        /* ignore */
      }
      selectedFacetIds.forEach(facetId => {
        const facet = FACETS_BY_ID.get(facetId);
        if (facet?.kind === 'keyword' && facet.count < keywordMinHits) {
          selectedFacetIds.delete(facetId);
        }
      });
      render();
    });

    clearFacets.addEventListener('click', function() {
      selectedFacetIds.clear();
      expandedTopLevelFacetIds.clear();
      expandedHierarchyFacetIds.clear();
      selectedRowId = null;
      hoveredRowId = null;
      hoveredGroupNodeId = null;
      render();
    });

    keywordFacetSort.addEventListener('change', () => renderFacetList(getVisibleRows()));

    if (slider.noUiSlider) {
      slider.noUiSlider.on('update', render);
    }

    timelineToggle.addEventListener('change', function() {
      slider.style.display = this.checked ? 'block' : 'none';
      render();
    });

    timelineToggle.checked = false;
    slider.style.display = 'none';
    render();

    let lastModified = null;

    async function checkForUpdate() {
      try {
        const response = await fetch(window.location.href, { method: 'HEAD' });
        const newModified = response.headers.get('Last-Modified');
        if (lastModified && newModified !== lastModified) {
          location.reload();
        }
        lastModified = newModified;
      } catch (e) {
        console.warn('Update check failed', e);
      }
    }

    setInterval(checkForUpdate, 2000);
  </script>

  <div id="copyright">Rhizomatica. Copyright 2026, Evan Brandon.</div>
</body>
</html>
'''


def build_html(collection_title, rows, facets, default_min_keyword_hits=None):
    if default_min_keyword_hits is None:
        default_min_keyword_hits = default_keyword_min_hits(facets)
    dates = sorted({d for row in rows for d in [row['begin'], row['end']] if d})

    def script_json(value):
        return (
            json.dumps(value, ensure_ascii=False)
            .replace("&", "\\u0026")
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
        )

    html = HTML_TEMPLATE.replace("__TITLE__", escape_html(collection_title))
    html = html.replace("__ROWS__", script_json(rows))
    html = html.replace("__FACETS__", script_json(facets))
    html = html.replace("__DEFAULT_KEYWORD_MIN_HITS__", str(default_min_keyword_hits))
    html = html.replace("__DATES__", script_json(dates))
    return html


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate multifacet network HTML pages from input/*-ead.xml")
    parser.add_argument("--open", action="store_true", help="Open each generated HTML page in your default browser")
    parser.add_argument(
        "--keyword-min-count",
        type=int,
        default=None,
        help="Set the initial keyword facet minimum in hits (overrides the 50-facet default).",
    )
    parser.add_argument(
      "--keyword-min-ratio",
      type=float,
      default=None,
      help="Set the initial keyword hit minimum as a ratio of files for this run (0-1).",
    )
    parser.add_argument(
        "--redundancy-ratio",
        type=float,
        default=None,
        help="Override config.json's facet_redundancy_ratio for this run (0-1).",
    )
    args = parser.parse_args(argv)

    input_files = sorted(INPUT_DIR.glob("*-ead.xml"))
    if not input_files:
        raise FileNotFoundError(f"No input files matching *-ead.xml found in {INPUT_DIR}")

    config = load_config()
    if args.keyword_min_count is not None and args.keyword_min_count < 1:
        parser.error("--keyword-min-count must be at least 1")
    if args.keyword_min_ratio is not None and (
        not math.isfinite(args.keyword_min_ratio) or not 0 <= args.keyword_min_ratio <= 1
    ):
        parser.error("--keyword-min-ratio must be between 0 and 1")
    facet_redundancy_ratio = args.redundancy_ratio if args.redundancy_ratio is not None else config["facet_redundancy_ratio"]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    failures = []
    for input_path in input_files:
        output_name = f"{input_path.name[:-len('-ead.xml')]}-rhizome.html"
        output_path = OUTPUT_DIR / output_name
        try:
            tree = ET.parse(input_path)
            root = strip_namespaces(tree.getroot())
            title, rows, facets = build_records(root, facet_redundancy_ratio)
            if not rows:
                raise ValueError("input produced no file-level rows to render")

            default_min_keyword_hits = default_keyword_min_hits(facets)
            if args.keyword_min_count is not None:
                default_min_keyword_hits = args.keyword_min_count
            elif args.keyword_min_ratio is not None:
                default_min_keyword_hits = max(1, math.ceil(len(rows) * args.keyword_min_ratio))
            html = build_html(title, rows, facets, default_min_keyword_hits)
            output_path.write_text(html, encoding="utf-8")
        except (ET.ParseError, OSError, ValueError) as exc:
            failures.append(input_path.name)
            print(f"Failed: {input_path.name} ({exc})")
            continue

        print(f"{input_path.name}: {len(rows)} rows, {len(facets)} facets")
        print(f"Generated: {output_path}")
        if args.open:
            webbrowser.open(output_path.resolve().as_uri())

    if failures:
        print(f"Failed to generate {len(failures)} of {len(input_files)} files.")
        return 1

    print(f"Generated {len(input_files)} rhizome file(s) in {OUTPUT_DIR}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())