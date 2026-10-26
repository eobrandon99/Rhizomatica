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
- Words that recur across multiple files' titles, general notes, or
  scope-and-content text become keyword facets too (see keyword_min_file_ratio
  in config.json).

Input:
- Files named <prefix>-ead.xml in the input/ folder.
- config.json (optional) in the same folder, for keyword_min_file_ratio.

Output:
- Files named <prefix>-rhizome.html in the output/ folder.
"""

from __future__ import annotations

import argparse
import calendar
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
  "keyword_min_file_ratio": 0.1,
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
    both each few more most other some such only own same other include includes inc inc.
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

    """.split()
)

C_LEVEL_RE = re.compile(r"^c\d*$")
WORD_RE = re.compile(r"[a-zA-Z'’]+")
SEGMENT_SPLIT_RE = re.compile(r"[,:;()\[\]\"/]+|\.\s+")
# Dotted abbreviations like "U.S." or "U.S.A." have no space between the
# periods, so they'd otherwise tokenize as separate single letters ("U",
# "S") that never meet the single-word length filter. Collapse them to
# "US" / "USA" before tokenizing so they're recognized as one acronym --
# and so they match up with "US"/"United States" elsewhere in the title.
ABBREVIATION_RE = re.compile(r"\b(?:[A-Za-z]\.){2,}(?![A-Za-z])")
AO_URI_RE = re.compile(r"/archival_objects/(\d+)")
EXTENT_NUMBER_RE = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
MAX_NGRAM = 4
MAX_ACRONYM_EXPANSION_NGRAM = 8


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
    date_expr = ""
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


def walk(node, ancestor_facets, inherited_date, rows, facets_by_id, hierarchy_parent_by_id, counter):
    children = [child for child in node if C_LEVEL_RE.match(local_tag(child))]
    did_fields = extract_did_fields(node)
    has_title = bool(did_fields["title"])
    has_own_date = bool(did_fields["date_expr"] or did_fields["begin"])

    if not children:
        counter[0] += 1
        row_id = unitid_facet_id(node, did_fields["title"] or f"file_{counter[0]}")
        file_title = did_fields["title"] or (facets_by_id.get(ancestor_facets[-1], "") if ancestor_facets else "")
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
        walk(child, next_ancestor_facets, next_inherited_date, rows, facets_by_id, hierarchy_parent_by_id, counter)


def normalize_abbreviations(text):
    return ABBREVIATION_RE.sub(lambda m: m.group(0).replace(".", ""), text)


def tokenize_title_segments(title):
    """Split a title into word-sequences, breaking at punctuation so n-grams
    never bridge across it (e.g. "Guide, UM" in "...Guide, UM EXPRES 1.0")."""
    title = normalize_abbreviations(title)
    segments = []
    for chunk in SEGMENT_SPLIT_RE.split(title):
        words = WORD_RE.findall(chunk)
        if words:
            segments.append(words)
    return segments


def tokenize_row_segments(row):
    for field in ("title", "general_note", "scope_content"):
        yield from tokenize_title_segments(row.get(field) or "")


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


def ngram_facet_id(chunk):
    parts = [re.sub(r"[^a-z0-9]+", "", w) or "x" for w in chunk]
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
    dominant "EXPRES" facet instead of fragmenting it."""
    counts = {chunk: len(row_ids) for chunk, row_ids in final_chunks.items()}
    chunks = list(counts)

    drop_target = {}
    for chunk in chunks:
        dominant = None
        for other in chunks:
            if other == chunk or counts[other] <= counts[chunk]:
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
    return dict(merged)


def extract_keyword_facets(rows, min_file_count, redundancy_ratio, collection_title=""):
    """Entity + frequency hybrid: find recurring words AND phrases in file
  titles, general notes, and scope-and-content text and turn them into facets.

    Possessives and common English noun plurals are normalized before
    candidates are counted and matched, so variants share a facet labeled
    with the most frequently occurring original form.

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
      so a word consumed by a longer phrase doesn't also surface as its
      own facet (see MAX_NGRAM).
    """
    candidate_rows = defaultdict(set)
    candidate_label_counts = defaultdict(lambda: defaultdict(int))
    candidate_label_spellings = {}
    extended_expansion_rows = defaultdict(lambda: defaultdict(set))
    acronym_seen = set()
    acronym_occurrence_rows = defaultdict(set)
    collection_title_chunks = set()
    for words in tokenize_title_segments(collection_title):
        normalized = [normalize_keyword_word(word).lower() for word in words]
        for length in range(1, len(normalized) + 1):
            for i in range(len(normalized) - length + 1):
                collection_title_chunks.add(tuple(normalized[i:i + length]))
        title_initials = facet_initials(" ".join(normalized))
        if len(title_initials) >= 2:
            collection_title_chunks.add((title_initials,))

    for row in rows:
        for words in tokenize_row_segments(row):
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
                    acronym_occurrence_rows[lowered[i]].add(row["id"])
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
                    candidate_rows[chunk].add(row["id"])
                    surface_label = " ".join(surface_words[i:i + length])
                    surface_key = surface_label.casefold()
                    candidate_label_counts[chunk][surface_key] += 1
                    candidate_label_spellings.setdefault((chunk, surface_key), surface_label)
            for length in range(MAX_NGRAM + 1, min(MAX_ACRONYM_EXPANSION_NGRAM, n) + 1):
                for i in range(0, n - length + 1):
                    chunk = tuple(lowered[i:i + length])
                    acronym = facet_initials(" ".join(normalized[i:i + length]))
                    if len(acronym) >= 2:
                        extended_expansion_rows[acronym][chunk].add(row["id"])

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
            and chunk not in collection_title_chunks
        )
    }

    facet_rows = defaultdict(set)
    for row in rows:
        matched_chunks = set()
        for words in tokenize_row_segments(row):
            lowered = [normalize_keyword_word(word).lower() for word in words]
            lowered = [word for word in lowered if word]
            n = len(lowered)
            i = 0
            while i < n:
                step = 1
                for length in range(min(MAX_NGRAM, n - i), 0, -1):
                    chunk = tuple(lowered[i:i + length])
                    if chunk in qualifying:
                        matched_chunks.add(chunk)
                        step = length
                        break
                i += step
        for chunk in matched_chunks:
            facet_rows[chunk].add(row["id"])

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

    final_chunks = {chunk: row_ids for chunk, row_ids in facet_rows.items() if len(row_ids) >= min_file_count}
    final_chunks = merge_redundant_facets(final_chunks, redundancy_ratio)

    rows_by_id = {row["id"]: row for row in rows}
    keyword_facets = {}
    for chunk, row_ids in final_chunks.items():
        facet_id = ngram_facet_id(chunk)
        keyword_facets[facet_id] = candidate_label[chunk]
        for row_id in row_ids:
            rows_by_id[row_id]["facets"].append(facet_id)

    return keyword_facets


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
        return

    for row in rows:
        deduped = []
        for facet_id in row["facets"]:
            target = id_remap.get(facet_id, facet_id)
            if target not in deduped:
                deduped.append(target)
        row["facets"] = deduped

    for facet_id in id_remap:
        facets_by_id.pop(facet_id, None)


def normalize_label(label):
    return re.sub(r"\s+", " ", label.strip().lower())


def propagate_embedded_facet_memberships(rows, facets_by_id):
  words_by_facet = {
    facet_id: tuple(WORD_RE.findall(normalize_label(label)))
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

    apply_facet_remap(rows, facets_by_id, id_remap)


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

    apply_facet_remap(rows, facets_by_id, id_remap)


def merge_duplicate_facets(rows, facets_by_id):
    """Merge duplicate keyword memberships while preserving hierarchy nodes."""
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

    apply_facet_remap(rows, facets_by_id, id_remap)


def build_records(root, keyword_min_file_count, facet_redundancy_ratio, keyword_min_file_ratio=DEFAULT_CONFIG["keyword_min_file_ratio"]):
    archdesc = root.find("archdesc")
    collection_title = text_of(archdesc.find("did/unittitle")) if archdesc is not None else "Finding Aid Network"

    dsc = archdesc.find("dsc") if archdesc is not None else None
    rows = []
    facets_by_id = {}
    hierarchy_parent_by_id = {}
    counter = [0]
    if dsc is not None:
        for child in dsc:
            if C_LEVEL_RE.match(local_tag(child)):
                walk(child, [], None, rows, facets_by_id, hierarchy_parent_by_id, counter)

    if keyword_min_file_count is None:
      keyword_min_file_count = max(1, math.ceil(len(rows) * keyword_min_file_ratio))

    keyword_facets = extract_keyword_facets(
        rows, keyword_min_file_count, facet_redundancy_ratio, collection_title
    )
    facets_by_id.update(keyword_facets)

    merge_same_label_facets(rows, facets_by_id)
    merge_acronym_facets(rows, facets_by_id)
    propagate_embedded_facet_memberships(rows, facets_by_id)
    merge_duplicate_facets(rows, facets_by_id)

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
        }
        for facet_id, label in facets_by_id.items()
    ]

    return collection_title, rows, facets


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
    #themeToggleBox {
      position: fixed;
      top: 12px;
      right: 16px;
      z-index: 1000;
      background: rgba(255,255,255,0.88);
      padding: 6px 10px;
      border-radius: 8px;
      backdrop-filter: blur(6px);
      box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08);
      font-size: 13px;
      user-select: none;
    }

    #themeToggleBox label {
      display: flex;
      align-items: center;
      gap: 8px;
      margin: 0;
      cursor: pointer;
      white-space: nowrap;
    }

    #themeToggleBox input[type="checkbox"] {
      margin: 0;
    }

    body[data-theme="dark"] #themeToggleBox {
      background: rgba(30,30,30,0.88);
      color: #fff;
      box-shadow: 0 1px 4px rgba(0, 0, 0, 0.35);
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
      flex: 0 0 auto;
      margin: 0;
      padding: 7px 8px;
      border-bottom: 1px solid #e0e0e0;
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: #555;
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
      color: #cbd5e1;
    }
    body[data-theme="dark"] .detail-panel.empty {
      background: transparent;
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
    <div id="themeToggleBox">
      <label for="themeToggle">
        <input type="checkbox" id="themeToggle">
        Dark mode
      </label>
    </div>
    <div id="toolbar">
    </div>
  </div>

  <div id="page">
    <aside id="facetSidebar">
      <div class="panel-heading">
        <div id="facetHeader">Facet spotlight</div>
        <button id="clearFacets" class="toolbar-btn" disabled>Clear selection</button>
      </div>
      <div id="facetSections">
        <section class="facet-section" aria-labelledby="hierarchyFacetHeader">
          <h3 id="hierarchyFacetHeader" class="facet-section-header">Finding aid hierarchy</h3>
          <div id="hierarchyFacetList" class="facet-section-list"></div>
        </section>
        <section class="facet-section" aria-labelledby="keywordFacetHeader">
          <h3 id="keywordFacetHeader" class="facet-section-header">Keyword facets</h3>
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
    const availableDates = __DATES__;

    const container = document.getElementById('container');
    const slider = document.getElementById('slider');
    const rangeText = document.getElementById('rangeText');
    const timelineToggle = document.getElementById('timelineToggle');
    const clearFacets = document.getElementById('clearFacets');
    const themeToggle = document.getElementById('themeToggle');
    const hierarchyFacetList = document.getElementById('hierarchyFacetList');
    const keywordFacetList = document.getElementById('keywordFacetList');
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
    const THEME_KEY = 'rhizomatica-theme';
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
        const childFiles = filesByFacet.get(child.id);
        if (!childFiles || childFiles.size === 0) return;

        let smallestSupersetSize = Infinity;
        const parentIds = [];
        facets.forEach(parent => {
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
    const HIERARCHY_ROOT_IDS = new Set(
      HIERARCHICAL_FACETS
        .filter(f => !PRIMARY_HIERARCHY_PARENT_BY_ID.has(f.id))
        .map(f => f.id)
    );
    const COLLAPSIBLE_HIERARCHY_ROOT_IDS = new Set(
      Array.from(HIERARCHY_ROOT_IDS).filter(rootId => (HIERARCHY_CHILDREN_BY_PARENT.get(rootId) || []).length > 0)
    );
    const expandedHierarchyRootIds = new Set();

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
      return rows.filter(row => {
        if (!row.facets.length) return true;
        return row.facets.some(facetId => {
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
        return;
      }

      selectedFacetIds.add(facetId);
      if (COLLAPSIBLE_ROOT_FACET_IDS.has(rootId)) expandedTopLevelFacetIds.add(rootId);
    }

    function buildNetworkData(rows, matchingRows) {
      const visibleFacetIds = new Set();
      const edges = [];
      const selectedRow = ROWS.find(r => r.id === selectedRowId) || null;
      const hoveredRow = ROWS.find(r => r.id === hoveredRowId) || null;

      matchingRows.forEach(row => row.facets.forEach(facetId => {
        const rootId = TOP_LEVEL_FACET_ID_BY_ID.get(facetId);
        if (COLLAPSIBLE_ROOT_FACET_IDS.has(rootId)) visibleFacetIds.add(rootId);
      }));

      const nodes = rows.map(row => {
        row.facets.forEach(fid => {
          if (isFacetBranchExpanded(fid)) visibleFacetIds.add(fid);
        });
        const highlighted = (selectedRow && selectedRow.id === row.id) || (hoveredRow && hoveredRow.id === row.id);
        return {
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
        };
      });

      FACETS.forEach(f => {
        if (isFacetNodeVisible(f.id) && (visibleFacetIds.has(f.id) || selectedFacetIds.has(f.id))) {
          const isKeywordFacet = f.kind === 'keyword';
          const selected = selectedFacetIds.has(f.id);
          const size = facetNodeSize(f.count, selected);
          const background = isKeywordFacet ? getTheme().keywordFacetBackground : getTheme().facetBackground;
          const border = isKeywordFacet ? getTheme().keywordFacetBorder : getTheme().facetBorder;
          const hoverBackground = isKeywordFacet ? getTheme().keywordFacetHoverBackground : getTheme().facetHoverBackground;
          const selectedBackground = isKeywordFacet ? getTheme().keywordFacetSelectedBackground : getTheme().facetSelectedBackground;
          const selectedBorder = isKeywordFacet ? getTheme().keywordFacetSelectedBorder : getTheme().facetSelectedBorder;
          nodes.push({
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

      const visibleFacetSet = new Set(nodes.filter(n => n.group === 'facet').map(n => n.id));
      rows.forEach(row => {
        const rowFacetIds = new Set(row.facets);
        row.facets.forEach(fid => {
          if (!isFacetNodeVisible(fid) || !visibleFacetSet.has(fid)) return;
          const hasVisibleMoreSpecificRowFacet = (FACET_CHILDREN_BY_PARENT.get(fid) || [])
            .some(childId => rowFacetIds.has(childId) && isFacetNodeVisible(childId));
          if (hasVisibleMoreSpecificRowFacet) return;
          edges.push({
            from: row.id,
            to: fid,
            width: selectedFacetIds.has(fid) ? 3 : 1.2,
            color: selectedFacetIds.has(fid) ? { color: getTheme().edgeSelected } : { color: getTheme().edge },
            title: `Connected facet: ${fid}`
          });
        });
      });

      FACET_CONTAINMENT_LINKS.forEach(link => {
        if (!visibleFacetSet.has(link.from) || !visibleFacetSet.has(link.to)) return;
        const child = FACETS.find(f => f.id === link.from);
        const parent = FACETS.find(f => f.id === link.to);
        if (!child || !parent) return;

        edges.push({
          from: link.from,
          to: link.to,
          arrows: { to: { enabled: true, scaleFactor: 0.6 } },
          dashes: [8, 5],
          width: 2.2,
          color: { color: getTheme().containmentEdge, opacity: 0.8 },
          title: `<b>Facet subset</b><br>Contained by: ${escapeHtml(parent.label)}<br>${child.count} files`
        });
      });

      return { nodes, edges };
    }

    function getCurrentDetailRow() {
      return ROWS.find(r => r.id === hoveredRowId) || ROWS.find(r => r.id === selectedRowId) || null;
    }

    function renderDetailPanel() {
      const row = getCurrentDetailRow();
      if (!row) {
        detailPanel.classList.add('empty');
        detailPanel.innerHTML = 'Hover or click a file node or row to preview details.';
        return;
      }

      const relatedFacets = (row.facets || []).map(fid => ({ id: fid, label: facetLabel(fid) }));

      detailPanel.classList.remove('empty');
      detailPanel.innerHTML = `
        <div class="detail-row"><span class="detail-label">Item name:</span> <span class="detail-value">${escapeHtml(row.title || row.id)}</span></div>
        <div class="detail-row"><span class="detail-label">Dates:</span> <span class="detail-value">${escapeHtml(row.display_date || 'undated')}</span></div>
        <div class="detail-row"><span class="detail-label">Container instance:</span> <span class="detail-value">${escapeHtml(row.container_instance || '—')}</span></div>
        ${(row.extents || []).length ? `<div class="detail-row"><span class="detail-label">Extents:</span> <span class="detail-value">${escapeHtml(row.extents.join('; '))}</span></div>` : ''}
        ${row.general_note ? `<div class="detail-row"><span class="detail-label">General note:</span> <span class="detail-value">${escapeHtml(row.general_note)}</span></div>` : ''}
        ${row.scope_content ? `<div class="detail-row"><span class="detail-label">Scope and content:</span> <span class="detail-value">${escapeHtml(row.scope_content)}</span></div>` : ''}
        <div class="detail-related">
          <div class="detail-related-title">Immediately related facets</div>
          ${relatedFacets.length ? `<div class="detail-facet-list">${relatedFacets.map(f => `<button type="button" class="detail-facet-chip ${selectedFacetIds.has(f.id) ? 'selected' : ''}" data-related-facet="${escapeHtml(f.id)}">${escapeHtml(f.label)}</button>`).join('')}</div>` : '<div class="detail-row"><span class="detail-value">No related facets.</span></div>'}
        </div>
      `;

      detailPanel.querySelectorAll('[data-related-facet]').forEach(btn => {
        btn.addEventListener('click', () => {
          const fid = btn.dataset.relatedFacet;
          toggleFacetSelection(fid);
          render();
        });
      });
    }

    function renderFacetList(rows) {
      const counts = buildFacetCounts(rows);
      const hasMatchingFiles = facet => selectedFacetIds.has(facet.id) || (counts.get(facet.id) || 0) > 0;
      const childrenByParent = HIERARCHY_CHILDREN_BY_PARENT;
      const rootFacets = [];

      HIERARCHICAL_FACETS.forEach(facet => {
        if (!PRIMARY_HIERARCHY_PARENT_BY_ID.has(facet.id) && hasMatchingFiles(facet)) rootFacets.push(facet);
      });

      function renderFacet(facet, topLevelRootId = null) {
        const selected = selectedFacetIds.has(facet.id);
        const children = (childrenByParent.get(facet.id) || [])
          .map(facetId => FACETS.find(candidate => candidate.id === facetId))
          .filter(child => child && hasMatchingFiles(child));
        const isTopLevel = topLevelRootId === null;
        const rootId = isTopLevel ? facet.id : topLevelRootId;
        const canCollapse = isTopLevel && COLLAPSIBLE_HIERARCHY_ROOT_IDS.has(rootId);
        const isExpanded = expandedHierarchyRootIds.has(rootId);
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
              ${canCollapse ? `<button type="button" class="facet-collapse-toggle" data-collapse-root="${rootId}" aria-label="${isExpanded ? 'Collapse' : 'Expand'} ${escapeHtml(facet.label)}" aria-expanded="${isExpanded}" title="${isExpanded ? 'Collapse' : 'Expand'} subsidiary facets"><span class="facet-collapse-chevron" aria-hidden="true"></span></button>` : ''}
            </div>
            ${children.length && (!canCollapse || isExpanded) ? `<div class="facet-children">${children.map(child => renderFacet(child, rootId)).join('')}</div>` : ''}
          </div>
        `;
      }

      const facetButton = facet => `
        <button type="button" class="facet-pill ${facet.kind === 'keyword' ? 'keyword-facet' : ''} ${selectedFacetIds.has(facet.id) ? 'selected' : ''}" data-facet-id="${escapeHtml(facet.id)}">
          <span class="facet-label">${escapeHtml(facet.label)}</span>
          <span class="count">${counts.get(facet.id) || 0}</span>
        </button>
      `;

      hierarchyFacetList.innerHTML = rootFacets.map(facet => renderFacet(facet)).join('');
      const keywordFacets = FACETS
        .filter(facet => facet.kind === 'keyword' && hasMatchingFiles(facet))
        .sort((a, b) => a.label.localeCompare(b.label));
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

      hierarchyFacetList.querySelectorAll('[data-collapse-root]').forEach(btn => {
        btn.addEventListener('click', () => {
          const rootId = btn.dataset.collapseRoot;
          if (expandedHierarchyRootIds.has(rootId)) {
            expandedHierarchyRootIds.delete(rootId);
          } else {
            expandedHierarchyRootIds.add(rootId);
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

      rowList.innerHTML = rows.map(row => {
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
      }).join('');

      rowList.querySelectorAll('[data-row-id]').forEach(btn => {
        const rowId = btn.dataset.rowId;
        btn.addEventListener('mouseenter', () => {
          hoveredRowId = rowId;
          renderDetailPanel();
        });
        btn.addEventListener('mouseleave', () => {
          if (hoveredRowId === rowId && selectedRowId !== rowId) {
            hoveredRowId = null;
            renderDetailPanel();
          }
        });
        btn.addEventListener('click', () => {
          selectedRowId = rowId;
          hoveredRowId = rowId;
          network.focus(selectedRowId, { scale: 1.2, animation: true });
          render();
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

      if (selectedRowId && !visibleRows.some(r => r.id === selectedRowId)) selectedRowId = null;
      if (hoveredRowId && !visibleRows.some(r => r.id === hoveredRowId)) hoveredRowId = null;

      renderFacetList(matchingRows);
      renderRowList(visibleRows, matchingRows.length);
      renderDetailPanel();

      const data = buildNetworkData(visibleRows, matchingRows);
      network.setData({
        nodes: new vis.DataSet(data.nodes),
        edges: new vis.DataSet(data.edges)
      });

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
        const isFacet = FACETS.some(f => f.id === clickedId);
        if (isFacet) {
          toggleFacetSelection(clickedId);
          selectedRowId = null;
          hoveredRowId = null;
          render();
          return;
        }

        selectedRowId = clickedId;
        hoveredRowId = clickedId;
        render();
        return;
      }

      if (params.nodes.length === 0 && params.edges.length === 0) {
        if (!selectedFacetIds.size) return;
        selectedFacetIds.clear();
        expandedTopLevelFacetIds.clear();
        selectedRowId = null;
        hoveredRowId = null;
        render();
      }
    });

    network.on('hoverNode', function(params) {
      const hoveredId = params.node;
      const isFacet = FACETS.some(f => f.id === hoveredId);
      if (!isFacet) {
        hoveredRowId = hoveredId;
        renderDetailPanel();
      }
    });

    network.on('blurNode', function(params) {
      const blurredId = params.node;
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

    clearFacets.addEventListener('click', function() {
      selectedFacetIds.clear();
      expandedTopLevelFacetIds.clear();
      selectedRowId = null;
      hoveredRowId = null;
      render();
    });

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


def build_html(collection_title, rows, facets):
    dates = sorted({d for row in rows for d in [row['begin'], row['end']] if d})
    html = HTML_TEMPLATE.replace("__TITLE__", collection_title)
    html = html.replace("__ROWS__", json.dumps(rows, ensure_ascii=False))
    html = html.replace("__FACETS__", json.dumps(facets, ensure_ascii=False))
    html = html.replace("__DATES__", json.dumps(dates, ensure_ascii=False))
    return html


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate multifacet network HTML pages from input/*-ead.xml")
    parser.add_argument("--open", action="store_true", help="Open each generated HTML page in your default browser")
    parser.add_argument(
        "--keyword-min-count",
        type=int,
        default=None,
      help="Use a fixed keyword facet minimum instead of the configured file-count ratio.",
    )
    parser.add_argument(
      "--keyword-min-ratio",
      type=float,
      default=None,
      help="Override config.json's keyword_min_file_ratio for this run (0-1).",
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
    keyword_min_file_count = args.keyword_min_count
    if keyword_min_file_count is None and args.keyword_min_ratio is None:
      keyword_min_file_count = config.get("keyword_min_file_count")
    keyword_min_file_ratio = args.keyword_min_ratio if args.keyword_min_ratio is not None else config["keyword_min_file_ratio"]
    if not math.isfinite(keyword_min_file_ratio) or not 0 <= keyword_min_file_ratio <= 1:
      parser.error("keyword_min_file_ratio must be between 0 and 1")
    facet_redundancy_ratio = args.redundancy_ratio if args.redundancy_ratio is not None else config["facet_redundancy_ratio"]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    failures = []
    for input_path in input_files:
        output_name = f"{input_path.name[:-len('-ead.xml')]}-rhizome.html"
        output_path = OUTPUT_DIR / output_name
        try:
            tree = ET.parse(input_path)
            root = strip_namespaces(tree.getroot())
            title, rows, facets = build_records(root, keyword_min_file_count, facet_redundancy_ratio, keyword_min_file_ratio)
            if not rows:
                raise ValueError("input produced no file-level rows to render")

            html = build_html(title, rows, facets)
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