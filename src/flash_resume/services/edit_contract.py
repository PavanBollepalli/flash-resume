"""The bullet-edit contract: what the model may change, and how the answer is checked.

Every rejected bullet edit is compute the run already paid for and threw away.
The gates in :mod:`flash_resume.services.tailor` run only after the model has
answered, and three of the four reasons they used to fire were things the model
had never been told: which bullets were editable, which facts a rewrite had to
keep, and how much room it had. This module hands the model that information up
front — one manifest line per editable bullet — and then checks the answer
against the same line, so what is left is a judgement call rather than a missing
instruction.

Two rules keep this honest rather than merely stricter:

* The master resume's own text decides. An edit's identity and its original
  bullet are resolved engine-side, so a paraphrased echo is repaired, never
  rejected.
* A declaration is never a new way to fail. ``preserved_facts`` makes the model
  enumerate what must survive and sharpens the rejection reason, but the text is
  what gets checked — an edit with an incomplete echo that keeps every fact is
  still accepted, and counted as such.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Optional, Sequence

from flash_resume.models.resume import MasterResume
from flash_resume.models.tailoring import BulletOrder, ContentPriority
from flash_resume.services.evidence import contains_term
from flash_resume.services.validator import validate_bullet_length


# A replacement may grow by two words, and may not exceed the width the layout
# still absorbs. ``validate_bullet_length`` allows 1.12x; the prompt gets a
# slightly tighter character allowance because the model cannot measure glyph
# widths, and a guide that is stricter than the gate can only prevent
# rejections, never cause one.
WORD_ALLOWANCE = 2
WIDTH_ALLOWANCE = 1.12
PROMPT_WIDTH_ALLOWANCE = 1.08
MIN_CHAR_HEADROOM = 6

# The floor matters as much as the ceiling. Given only an upper bound, a model
# asked to place a keyword buys the room by deleting what the bullet names —
# "high-throughput ... across Google Drive, AWS S3, and Salesforce" became
# "across cloud platforms" — and the rewrite is then refused for losing 30% of
# the width. Stating the floor up front is what stops that being paid for twice.
MIN_WORD_DELTA = -4
MIN_WIDTH_RATIO = 0.75
PROMPT_MIN_WORD_DELTA = -3
PROMPT_MIN_WIDTH_RATIO = 0.80

# Ordering and relevance are derived locally, so the caps below only shape the
# report: they keep a keyword-stuffed bullet from outranking real evidence.
TERM_CREDIT_CAP = 3

_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}

# A resume carries its technologies twice: as the structured ``technologies``
# field, and — in a hand-written resume — as a trailing "Stack: ..." bullet. The
# template renders that bullet as the Technologies line and suppresses its own
# Tech Stack line when it is present.
_STACK_PREFIXES = ("stack:", "tech stack:", "technologies:", "technology stack:")


def is_stack_bullet(text: str) -> bool:
    """Whether a bullet is the project's technology line rather than an outcome.

    It matters because that line names every technology at once, so ranking
    bullets by how much JD evidence they carry promotes it above the
    accomplishments — a recruiter would read a list of tools before a single
    result. It is metadata, so it stays last and is not offered for rewriting.
    """
    value = (text or "").strip().lstrip("*").strip().casefold()
    return value.startswith(_STACK_PREFIXES)

# Rejection codes are written for the funnel report, so each one is phrased as
# the rule the model broke rather than as the internal name of the gate.
REJECTION_LABELS = {
    "unresolved_target": "the edit named no bullet that exists in the resume",
    "no_absorbable_term": "no supported JD term was left to add to that bullet",
    "facts_dropped": "a metric or outcome verb was dropped",
    "no_new_evidence": "no new supported JD evidence",
    "unsupported_claim": "claimed evidence the resume does not have",
    "over_budget": "over the word or width budget",
}

# Trailing ``\b`` is deliberately absent: a percent sign is not a word
# character, so ``\b`` after it never matches and the pattern would silently
# report "40" for "40%" — the manifest would then ask the model to keep a
# metric it never saw, and a rejection would name the wrong fact.
_NUMBER_PATTERN = r"\b\d+(?:\.\d+)?%?"

OUTCOME_PATTERNS: tuple[tuple[str, str], ...] = (
    ("achieve", r"\bachiev(?:e|es|ed|ing)\b"),
    ("automate", r"\bautomat(?:e|es|ed|ing)\b"),
    ("decrease", r"\bdecreas(?:e|es|ed|ing)\b"),
    ("eliminate", r"\beliminat(?:e|es|ed|ing)\b"),
    ("improve", r"\bimprov(?:e|es|ed|ing)\b"),
    ("increase", r"\bincreas(?:e|es|ed|ing)\b"),
    ("optimize", r"\boptimiz(?:e|es|ed|ing)\b"),
    ("publish", r"\bpublish(?:e|es|ed|ing)\b"),
    ("reduce", r"\breduc(?:e|es|ed|ing)\b"),
    ("resolve", r"\bresolv(?:e|es|ed|ing)\b"),
    ("save", r"\bsav(?:e|es|ed|ing)\b"),
    ("ship", r"\bshipp(?:e|s|ed|ing)\b"),
)


def protected_facts(text: str) -> tuple[str, ...]:
    """Concrete facts a rewrite must not silently drop, in first-seen order.

    Numbers are the metrics a recruiter reads; a matched outcome verb is the
    claim the bullet is making. Both must still be present afterwards, which is
    exactly what :func:`missing_facts` checks.
    """
    facts: list[str] = re.findall(_NUMBER_PATTERN, text)
    for label, pattern in OUTCOME_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            facts.append(f"{label} outcome")
    seen: set[str] = set()
    ordered: list[str] = []
    for fact in facts:
        key = fact.casefold()
        if key not in seen:
            seen.add(key)
            ordered.append(fact)
    return tuple(ordered)


def missing_facts(original: str, replacement: str) -> list[str]:
    """Protected facts of ``original`` that ``replacement`` no longer carries.

    Every metric must survive, and at least one of the bullet's outcome claims
    must — compared by the claim rather than the verb form, so "publishes" ->
    "publishing" keeps the claim and does not read as a loss. Demanding every
    outcome verb back would reject good compactions ("cutting deploy time 40%"
    for "reduced deploy time by 40% by automating the release").
    """
    lost = sorted(set(_numbers(original)) - set(_numbers(replacement)))
    original_outcomes = {
        label
        for label, pattern in OUTCOME_PATTERNS
        if re.search(pattern, original, re.IGNORECASE)
    }
    if original_outcomes and not original_outcomes & {
        label
        for label, pattern in OUTCOME_PATTERNS
        if re.search(pattern, replacement, re.IGNORECASE)
    }:
        lost.extend(f"{label} outcome" for label in sorted(original_outcomes))
    return lost


def _numbers(text: str) -> set[str]:
    return {value.casefold() for value in re.findall(_NUMBER_PATTERN, text)}


def has_outcome(text: str) -> bool:
    """Whether the text makes a measurable claim of improvement or delivery."""
    return any(re.search(pattern, text, re.IGNORECASE) for _, pattern in OUTCOME_PATTERNS)


@dataclass(frozen=True)
class BulletTarget:
    """One editable bullet, plus everything the model needs to edit it safely."""

    section: str
    item_id: str
    bullet_index: int
    text: str
    keep: tuple[str, ...]
    can_add: tuple[str, ...]

    @property
    def key(self) -> str:
        """Handle the model refers to this bullet by, e.g. ``exp_acme#2``."""
        return f"{self.item_id}#{self.bullet_index}"

    @property
    def max_words(self) -> int:
        return len(self.text.split()) + WORD_ALLOWANCE

    @property
    def max_chars(self) -> int:
        return max(len(self.text) + MIN_CHAR_HEADROOM, int(len(self.text) * PROMPT_WIDTH_ALLOWANCE))

    @property
    def min_words(self) -> int:
        """Fewest words the rewrite may keep, a step above the gate's floor."""
        return max(1, len(self.text.split()) + PROMPT_MIN_WORD_DELTA)

    @property
    def min_chars(self) -> int:
        """Fewest characters the rewrite may keep.

        Deliberately above ``MIN_WIDTH_RATIO``: a rewrite that honours the
        stated floor cannot trip the gate, so the only over-trimming that gets
        refused is over-trimming the model was warned about.
        """
        return int(len(self.text) * PROMPT_MIN_WIDTH_RATIO)


def absorbable_terms(assessment: Sequence) -> list[str]:
    """Requirements a bullet could credibly absorb, in assessment order.

    Generic professional skills are excluded: an assessment matches those as
    their own class with no resume evidence behind them, and inviting the model
    to write "Time management skills" into a bullet would be exactly the keyword
    stuffing this contract exists to prevent.
    """
    return [
        match.requirement
        for match in assessment
        if match.status != "unsupported" and match.match_method != "soft_skill"
    ]


def build_targets(
    resume: MasterResume, supported_terms: Sequence[str]
) -> list[BulletTarget]:
    """Describe every bullet in the resume as an edit target.

    Locked bullets (no term left to add) are included so their identity can
    still be resolved; :func:`render_manifest` is what hides them from the
    model's list of things it may rewrite.
    """
    terms = [term for term in supported_terms if term and term.strip()]
    targets: list[BulletTarget] = []
    for section, items in (("Experience", resume.experience), ("Projects", resume.projects)):
        for item in items:
            for index, bullet in enumerate(item.bullets):
                if is_stack_bullet(bullet):
                    # The technology line is metadata, not an accomplishment. It
                    # is never rewritten — rewriting it would just move names
                    # around — and it is not offered to the model as a target.
                    continue
                targets.append(
                    BulletTarget(
                        section=section,
                        item_id=item.id,
                        bullet_index=index,
                        text=bullet,
                        keep=protected_facts(bullet),
                        can_add=tuple(
                            term for term in terms if not contains_term(bullet, term)
                        ),
                    )
                )
    return targets


EDIT_CONTRACT_RULES = """BULLET EDIT CONTRACT (read before writing any bullet edit):
1. TARGETS is the complete list of bullets you may rewrite, and each line gives
   that bullet's item id, 0-based bullet index, the facts it must keep, the terms
   it may absorb, and its word/character budget. Bullets that are not listed are
   already carrying every JD term the resume can support: return no edit for them.
2. Spend an edit only to place a term from that bullet's can_add list. A rewrite
   that adds no term from can_add is wasted work and will be refused.
3. Before rewriting a target, copy every entry of its keep list into
   preserved_facts verbatim. Then make sure each of those entries still appears
   in the replacement. Dropping a metric or an outcome verb is never allowed,
   even to make room for a keyword.
4. Stay inside the target's word and character range. The upper bound is what the
   layout absorbs; the lower bound is what the bullet's own detail needs. Do not
   drop a named tool, system, or employer to make room for a keyword — a rewrite
   that gives away a third of the bullet is refused, so keep the named specifics
   and spend the budget on the new term.
5. Do not introduce a JD term the resume has no evidence for anywhere, and never
   invent a tool, metric, employer, or responsibility.
6. Return at most 4 edits — the highest-value ones first. An edit that changes
   emphasis without placing a new supported term is worth zero, so omit it.
"""


def render_manifest(targets: Sequence[BulletTarget]) -> str:
    """Render the editable targets as the compact TARGETS block for the prompt."""
    editable = [target for target in targets if target.can_add]
    if not editable:
        return (
            "TARGETS: none. Every bullet in this scope already carries the JD terms "
            "the resume can support, so return no bullet edits for this call."
        )
    lines = [
        "TARGETS (the only bullets you may rewrite; item id, then 0-based index):",
        "  target | keep | can_add | words | chars",
    ]
    for target in editable:
        keep = ", ".join(target.keep) or "none (no metric or outcome verb in this bullet)"
        lines.append(
            f"  {target.key} | keep: {keep} | can_add: {', '.join(target.can_add)}"
            f" | {target.min_words}-{target.max_words} words"
            f" | {target.min_chars}-{target.max_chars} chars"
        )
    return "\n".join(lines)


def resolve_target(
    edit, targets: Sequence[BulletTarget]
) -> tuple[Optional[BulletTarget], bool]:
    """Find the bullet an edit means, and whether that took a repair.

    Identity is ``(section, item_id, bullet_index)``. A model that paraphrases
    the original text, omits it, or points at the wrong index of a single-bullet
    item still lands on the right bullet — the engine substitutes the master
    resume's own copy of the text, so no edit is lost to a wording mismatch.
    """
    section = _section_of(edit.section)
    for target in targets:
        if (
            target.item_id == edit.item_id
            and target.bullet_index == edit.bullet_index
            and section in (None, target.section)
        ):
            return target, False

    text = (edit.original_text or "").strip()
    if text and text != edit.replacement_text.strip():
        matches = [target for target in targets if target.text.strip() == text]
        if section is not None:
            matches = [target for target in matches if target.section == section] or matches
        if len(matches) == 1:
            return matches[0], True

    if section is not None:
        siblings = [
            target
            for target in targets
            if target.item_id == edit.item_id and target.section == section
        ]
        if len(siblings) == 1:
            # A wrong index on an item that owns exactly one bullet is still
            # unambiguous, so treat it as the same target rather than as a miss.
            return siblings[0], True
    return None, False


def _section_of(section: str) -> Optional[str]:
    """Normalise the free-text section the model echoes back."""
    value = (section or "").casefold()
    if "exp" in value:
        return "Experience"
    if "proj" in value:
        return "Projects"
    return None


def echoes_keep_list(declared: Sequence[str], keep: Sequence[str]) -> bool:
    """Whether the model's ``preserved_facts`` echo covers the target's facts.

    An incomplete echo is deliberately *not* a rejection — the text is what
    proves the facts survived, and rejecting on bookkeeping would trade one
    silent loss for another. It is counted instead, so drift in how well the
    model follows the contract shows up in the funnel.
    """
    return {fact.casefold().strip() for fact in keep} <= {
        fact.casefold().strip() for fact in declared if fact and fact.strip()
    }


def unevidenced_terms_introduced(
    replacement: str, original: str, unsupported_terms: Iterable[str]
) -> list[str]:
    """JD terms a rewrite introduces while the resume has no evidence for them.

    Deliberately a *report*, not a gate. Whether the resume "has evidence" is
    decided by the matcher, and the matcher has blind spots: it does not link
    ``REST`` to the ``RESTful APIs`` already in the skills section, so a gate on
    this set would reject a rewrite that only states what the resume says. The
    terms are counted and named in the funnel instead, which shows whether the
    hole is being exercised before anything is closed on top of it.
    """
    return [
        term
        for term in unsupported_terms
        if contains_term(replacement, term) and not contains_term(original, term)
    ]


def adds_supported_evidence(
    replacement: str, original: str, supported_terms: Sequence[str]
) -> bool:
    """Whether a rewrite places new supported evidence or keeps what it had."""
    if any(
        contains_term(replacement, term) and not contains_term(original, term)
        for term in supported_terms
    ):
        return True
    original_hits = sum(contains_term(original, term) for term in supported_terms)
    return original_hits > 0 and sum(
        contains_term(replacement, term) for term in supported_terms
    ) >= original_hits


def within_budget(original: str, replacement: str) -> bool:
    """Whether a rewrite fits the layout, allowing limited safe compaction.

    The bounds here are the ones ``render_manifest`` quotes to the model, so a
    rewrite that follows its target line always passes. ``validate_bullet_length``
    is the stricter layout check and still runs first.
    """
    valid, word_delta, width_ratio = validate_bullet_length(original, replacement)
    if valid:
        return True
    return (
        word_delta >= MIN_WORD_DELTA
        and MIN_WIDTH_RATIO <= width_ratio <= WIDTH_ALLOWANCE
        and not missing_facts(original, replacement)
    )


def _ordered_bullets(
    bullets: Sequence[str], effective: dict[tuple[str, int], str], item_id: str
) -> list[str]:
    return [
        effective.get((item_id, index), bullet) for index, bullet in enumerate(bullets)
    ]


def derive_content_priorities(
    resume: MasterResume,
    supported_terms: Sequence[str],
    effective: Optional[dict[tuple[str, int], str]] = None,
) -> list[ContentPriority]:
    """Rank each item's JD relevance locally, from the terms its bullets carry.

    This replaces the ``content_priorities`` the model used to be asked for: the
    ranking is a plain count of supported evidence in the item's own bullets, and
    deriving it here means it can never disagree with the edit set or arrive
    truncated.
    """
    effective = effective or {}
    ranked: list[tuple[int, int, ContentPriority]] = []
    for section, items in (("Experience", resume.experience), ("Projects", resume.projects)):
        for position, item in enumerate(items):
            bullets = _ordered_bullets(item.bullets, effective, item.id)
            keywords = [
                term
                for term in supported_terms
                if any(contains_term(bullet, term) for bullet in bullets)
            ]
            if len(keywords) >= 2:
                priority = "high"
            elif keywords:
                priority = "medium"
            else:
                priority = "low"
            metrics = sum(1 for bullet in bullets if _numbers(bullet))
            reason = (
                f"{len(keywords)} supported JD term(s) across {len(bullets)} bullets"
                f"; {metrics} carry a metric"
                if keywords
                else "No supported JD term appears in this item's bullets"
            )
            ranked.append(
                (
                    _PRIORITY_RANK[priority],
                    position if section == "Experience" else len(resume.experience) + position,
                    ContentPriority(
                        section=section,
                        item_id=item.id,
                        priority=priority,
                        supported_keywords=keywords,
                        reason=reason,
                    ),
                )
            )
    # Strongest items first, then resume order, so the report reads as a ranking.
    return [priority for _, _, priority in sorted(ranked, key=lambda row: row[:2])]


def derive_bullet_orders(
    resume: MasterResume,
    supported_terms: Sequence[str],
    effective: Optional[dict[tuple[str, int], str]] = None,
) -> list[BulletOrder]:
    """Order each item's bullets by JD evidence density.

    Deterministic and free, unlike the ``bullet_orders`` permutations the model
    used to return — which the gates then dropped whenever they were incomplete.
    Metrics and outcome verbs break ties upward so a bullet that proves impact is
    not demoted below one that merely names a keyword. Items whose ranking does
    not change are omitted rather than restated.
    """
    effective = effective or {}
    orders: list[BulletOrder] = []
    for section, items in (("Experience", resume.experience), ("Projects", resume.projects)):
        for item in items:
            bullets = _ordered_bullets(item.bullets, effective, item.id)
            if len(bullets) < 2:
                continue
            # The technology line is metadata and always belongs last, so it is
            # pinned rather than ranked — otherwise its list of every tool in
            # the project outscores the accomplishments that follow it.
            content = [
                index for index, bullet in enumerate(bullets) if not is_stack_bullet(bullet)
            ]
            metadata = [
                index for index, bullet in enumerate(bullets) if is_stack_bullet(bullet)
            ]
            ranked = sorted(
                content,
                key=lambda index: (-_bullet_strength(bullets[index], supported_terms), index),
            ) + metadata
            if ranked != list(range(len(bullets))):
                orders.append(
                    BulletOrder(section=section, item_id=item.id, bullet_indices=ranked)
                )
    return orders


def _bullet_strength(text: str, supported_terms: Sequence[str]) -> int:
    hits = min(sum(contains_term(text, term) for term in supported_terms), TERM_CREDIT_CAP)
    strength = 3 * hits
    if _numbers(text):
        strength += 2
    if has_outcome(text):
        strength += 1
    return strength
