"""Tests for the bullet-edit contract: the manifest, the repairs, and the gates.

The point of these gates is that they reject a *judgement call*, not a missing
instruction, so most of what is asserted here is that a proposal the model could
not have known was wrong is repaired or reported rather than thrown away.
"""

from flash_resume.models.resume import (
    ContactInfo,
    ExperienceItem,
    MasterResume,
    ProjectItem,
    SkillCategory,
)
from flash_resume.models.tailoring import (
    BulletEdit,
    EditFunnel,
    JDKeywords,
    ProjectEditPlan,
    TailorPlan,
)
from flash_resume.services.edit_contract import (
    absorbable_terms,
    build_targets,
    derive_bullet_orders,
    derive_content_priorities,
    missing_facts,
    protected_facts,
    render_manifest,
    resolve_target,
)
from flash_resume.services.evidence import evaluate_requirements
from flash_resume.services.tailor import (
    generate_diff_markdown,
    generate_split_plan,
    sanitize_tailor_plan,
)


PYTHON_BULLET = (
    "Reduced API latency by 40% by optimizing the Python caching layer for the payments team."
)
OTHER_BULLET = "Partnered with design to ship the onboarding flow for new merchants."


def _resume(
    bullets=None,
    project_bullets=None,
    skills=None,
) -> MasterResume:
    """A resume small enough to reason about the manifest by hand.

    ``Docker`` is evidenced by the skills section but named by no bullet, which
    is the ordinary case the contract exists for: a supported term with somewhere
    left to put it.
    """
    return MasterResume(
        contact=ContactInfo(name="A Candidate", email="a@b.c", phone="+1", location="Remote"),
        summary="Backend engineer working on payment APIs.",
        skills=(
            skills
            if skills is not None
            else [
                SkillCategory(category="Languages", items=["Python"]),
                SkillCategory(category="Platform", items=["Docker"]),
            ]
        ),
        experience=[
            ExperienceItem(
                id="exp_acme",
                company="Acme",
                role="Backend Engineer",
                location="Remote",
                start_date="2024",
                end_date="Present",
                bullets=list(bullets if bullets is not None else [PYTHON_BULLET, OTHER_BULLET]),
            )
        ],
        projects=[
            ProjectItem(
                id="proj_cache",
                name="Cache",
                technologies=["Python"],
                bullets=list(project_bullets if project_bullets is not None else ["Built a cache."]),
            )
        ],
    )


def _targets(resume, requirements):
    assessment = evaluate_requirements(resume, JDKeywords(required_keywords=requirements))
    return assessment, build_targets(resume, absorbable_terms(assessment))


# -- the manifest -----------------------------------------------------------


def test_protected_facts_are_metrics_and_named_outcomes():
    facts = protected_facts(PYTHON_BULLET)

    assert "40%" in facts
    # The outcome is named as the claim, not as the verb form the bullet used.
    assert "reduce outcome" in facts
    assert "optimize outcome" in facts
    assert protected_facts("Partnered with design to ship onboarding.") == ()


def test_absorbable_terms_exclude_terms_the_bullet_already_carries():
    resume = _resume()
    assessment = evaluate_requirements(
        resume, JDKeywords(required_keywords=["Python", "Docker"])
    )

    targets = {target.key: target for target in build_targets(resume, absorbable_terms(assessment))}

    assert targets["exp_acme#0"].can_add == ("Docker",)
    # Python is backed by the resume but the bullet that has it cannot absorb it again.
    assert "Python" not in targets["exp_acme#0"].can_add
    assert targets["exp_acme#1"].can_add == ("Python", "Docker")


def test_unsupported_terms_are_never_absorbable():
    """Docker has evidence behind it; Kubernetes does not, so no bullet is
    invited to write it in."""
    resume = _resume()
    assessment = evaluate_requirements(
        resume, JDKeywords(required_keywords=["Docker", "Kubernetes"])
    )

    assert absorbable_terms(assessment) == ["Docker"]


def test_soft_skills_are_never_absorbable():
    """A generic expectation has no resume evidence behind it, so it is not a
    term a bullet may absorb — inviting one would be keyword stuffing."""
    resume = _resume()
    assessment = evaluate_requirements(
        resume, JDKeywords(required_keywords=["Strong communication skills"])
    )

    assert absorbable_terms(assessment) == []
    targets = build_targets(resume, absorbable_terms(assessment))
    assert all(not target.can_add for target in targets)


def test_manifest_hides_bullets_with_nothing_left_to_add():
    resume = _resume()
    assessment, targets = _targets(resume, ["Python"])

    manifest = render_manifest(targets)

    # "Python" is the only supported term and the first bullet already uses it,
    # so that bullet is locked and must not appear as something editable.
    assert "exp_acme#0" not in manifest
    assert "exp_acme#1" in manifest
    # Nothing locked leaks into the list: the manifest carries ids, facts, terms
    # and budgets only, so no bullet text can be mistaken for an invitation.
    assert PYTHON_BULLET not in manifest


def test_manifest_says_so_when_there_is_no_work():
    assert "TARGETS: none" in render_manifest(build_targets(_resume(), []))


def test_manifest_budget_is_the_exact_per_bullet_range():
    target = build_targets(_resume(), ["Docker"])[0]

    line = render_manifest([target])

    # A range, not a ceiling: the floor is what stops the model buying room for
    # a keyword by deleting the systems the bullet names.
    assert f"{target.min_words}-{target.max_words} words" in line
    assert f"{target.min_chars}-{target.max_chars} chars" in line


def test_the_stated_floor_is_stricter_than_the_gate():
    """Following the manifest can only prevent a rejection, never cause one."""
    from flash_resume.services.edit_contract import (
        MIN_WIDTH_RATIO,
        MIN_WORD_DELTA,
        PROMPT_MIN_WIDTH_RATIO,
        PROMPT_MIN_WORD_DELTA,
        WIDTH_ALLOWANCE,
    )

    assert MIN_WIDTH_RATIO < PROMPT_MIN_WIDTH_RATIO < WIDTH_ALLOWANCE
    assert MIN_WORD_DELTA < PROMPT_MIN_WORD_DELTA <= 0


def test_a_rewrite_at_the_stated_floor_passes_the_budget_gate():
    """The worst rewrite the manifest allows still clears the gate, which is what
    makes an over-budget rejection a fair one."""
    from flash_resume.services.edit_contract import within_budget

    target = build_targets(_resume(), ["Docker"])[0]
    trimmed = " ".join(target.text.split()[: target.min_words]) + " Docker"

    assert target.min_words <= len(trimmed.split()) <= target.max_words
    assert target.min_chars <= len(trimmed) <= target.max_chars
    assert within_budget(target.text, trimmed)


# -- resolving what the model meant -----------------------------------------


def test_resolve_target_uses_the_structured_handle():
    _, targets = _targets(_resume(), ["Docker"])

    edit = BulletEdit(
        section="Experience", item_id="exp_acme", bullet_index=1, replacement_text="x"
    )
    target, repaired = resolve_target(edit, targets)

    assert target.key == "exp_acme#1"
    assert repaired is False


def test_resolve_target_repairs_a_paraphrased_original_text():
    """A model that retypes the bullet slightly differently must not cost the run
    the edit: identity is the handle, and the engine substitutes its own text."""
    _, targets = _targets(_resume(), ["Docker"])

    edit = BulletEdit(
        section="Experience",
        item_id="exp_acme",
        bullet_index=1,
        original_text="Partnered with design, shipping onboarding for new merchants.",
        replacement_text="x",
    )
    target, repaired = resolve_target(edit, targets)

    assert target.bullet_index == 1
    assert repaired is False  # the handle was enough


def test_resolve_target_recovers_from_a_wrong_index_via_the_text():
    _, targets = _targets(_resume(), ["Docker"])

    edit = BulletEdit(
        section="Experience",
        item_id="exp_acme",
        bullet_index=7,
        original_text=OTHER_BULLET,
        replacement_text="x",
    )
    target, repaired = resolve_target(edit, targets)

    assert target.key == "exp_acme#1"
    assert repaired is True


def test_resolve_target_returns_nothing_for_an_unknown_item():
    _, targets = _targets(_resume(), ["Docker"])

    edit = BulletEdit(section="Experience", item_id="exp_nowhere", bullet_index=0, replacement_text="x")

    assert resolve_target(edit, targets) == (None, False)


# -- the gates --------------------------------------------------------------


def _sanitize(plan, resume, requirements, funnel=None):
    assessment = evaluate_requirements(resume, JDKeywords(required_keywords=requirements))
    return sanitize_tailor_plan(
        plan,
        resume,
        " ".join(requirements),
        JDKeywords(required_keywords=requirements),
        assessment=assessment,
        targets=build_targets(resume, absorbable_terms(assessment)),
        funnel=funnel,
    )


def test_relabelled_bullet_still_lands_and_echoes_the_engine_text():
    resume = _resume()
    plan = TailorPlan(
        bullet_edits=[
            BulletEdit(
                section="Experience",
                item_id="exp_acme",
                bullet_index=1,
                original_text="a paraphrase the model invented",
                replacement_text="Shipped the onboarding flow with design and Docker for merchants.",
                preserved_facts=["Docker"],
            )
        ]
    )

    sanitized = _sanitize(plan, resume, ["Docker"])

    assert len(sanitized.bullet_edits) == 1
    # The stored edit carries the master resume's own text, so the diff report
    # shows what actually changed.
    assert sanitized.bullet_edits[0].original_text == OTHER_BULLET


def test_an_over_budget_rewrite_is_refused_by_the_budget_gate():
    """The contract states each bullet's character budget, so a rewrite that
    ignores a number the model was handed is a fair rejection."""
    resume = _resume()
    plan = TailorPlan(
        bullet_edits=[
            BulletEdit(
                section="Experience",
                item_id="exp_acme",
                bullet_index=1,
                replacement_text=OTHER_BULLET.replace(
                    "design", "design and Docker and Kubernetes and Terraform"
                ),
            )
        ]
    )
    funnel = EditFunnel()

    sanitized = _sanitize(plan, resume, ["Docker"], funnel)

    assert not sanitized.bullet_edits
    assert funnel.reasons == {"over_budget": 1}


def test_locked_bullet_is_refused_with_its_own_reason():
    """A bullet that already carries every supported term cannot gain evidence."""
    resume = _resume(bullets=["Built the Python service."])
    plan = TailorPlan(
        bullet_edits=[
            BulletEdit(
                section="Experience",
                item_id="exp_acme",
                bullet_index=0,
                replacement_text="Built the Python service for payments.",
            )
        ]
    )
    funnel = EditFunnel()

    sanitized = _sanitize(plan, resume, ["Python"], funnel)

    assert not sanitized.bullet_edits
    assert "no supported JD term is left to add" in sanitized.rejected_bullet_edits[0].reason
    assert funnel.reasons == {"no_absorbable_term": 1}

def test_rewriting_a_bullet_to_itself_is_a_skip_not_a_rejection():
    resume = _resume()
    plan = TailorPlan(
        bullet_edits=[
            BulletEdit(
                section="Experience",
                item_id="exp_acme",
                bullet_index=1,
                replacement_text=OTHER_BULLET,
            )
        ]
    )
    funnel = EditFunnel()

    sanitized = _sanitize(plan, resume, ["Docker"], funnel)

    assert not sanitized.bullet_edits
    assert not sanitized.rejected_bullet_edits
    assert len(sanitized.skipped_bullet_edits) == 1
    assert funnel.skipped == 1


def test_dropping_a_metric_is_still_refused_and_names_the_metric():
    resume = _resume()
    plan = TailorPlan(
        bullet_edits=[
            BulletEdit(
                section="Experience",
                item_id="exp_acme",
                bullet_index=0,
                replacement_text="Optimized the caching layer with Docker for the payments team.",
            )
        ]
    )
    funnel = EditFunnel()

    sanitized = _sanitize(plan, resume, ["Docker"], funnel)

    assert not sanitized.bullet_edits
    assert "40%" in sanitized.rejected_bullet_edits[0].reason
    assert funnel.reasons == {"facts_dropped": 1}


def test_a_missing_facts_echo_is_counted_but_never_rejected():
    """Bookkeeping must not become a new way for a good edit to die."""
    resume = _resume()
    plan = TailorPlan(
        bullet_edits=[
            BulletEdit(
                section="Experience",
                item_id="exp_acme",
                bullet_index=0,
                replacement_text=(
                    "Optimized the Python caching layer, cutting API latency 40% with Docker."
                ),
                preserved_facts=[],
            )
        ]
    )
    funnel = EditFunnel()

    sanitized = _sanitize(plan, resume, ["Docker"], funnel)

    assert len(sanitized.bullet_edits) == 1
    assert funnel.applied == 1
    assert funnel.undeclared_facts == 1


def test_verb_form_changes_are_not_treated_as_lost_outcomes():
    """ "publishes" -> "publishing" keeps the claim."""
    assert missing_facts("Publishes to PyPI nightly.", "Publishing to PyPI nightly.") == []
    assert missing_facts("Reduced latency by 40%.", "Cut latency by 40%.") == ["reduce outcome"]


# -- what used to be asked of the model -------------------------------------


def test_content_priorities_are_derived_from_resume_evidence():
    resume = _resume(project_bullets=["Built a Docker Python cache."])

    priorities = derive_content_priorities(resume, ["Python", "Docker"])

    by_id = {priority.item_id: priority for priority in priorities}
    # The project names both supported terms; the experience names one.
    assert by_id["proj_cache"].priority == "high"
    assert by_id["proj_cache"].supported_keywords == ["Python", "Docker"]
    assert by_id["exp_acme"].priority == "medium"
    assert by_id["exp_acme"].supported_keywords == ["Python"]
    assert "1 supported JD term(s) across 2 bullets" in by_id["exp_acme"].reason
    # Strongest first, and the ranking cannot carry a score the edits contradict.
    assert [priority.item_id for priority in priorities] == ["proj_cache", "exp_acme"]


def test_bullet_ordering_puts_evidence_first_and_is_a_complete_permutation():
    resume = _resume(
        bullets=[
            OTHER_BULLET,
            PYTHON_BULLET,
            "Wrote documentation for the payments API.",
        ]
    )

    orders = derive_bullet_orders(resume, ["Python"])

    order = next(order for order in orders if order.item_id == "exp_acme")
    assert order.bullet_indices == [1, 0, 2]
    assert sorted(order.bullet_indices) == [0, 1, 2]


def test_bullet_ordering_is_omitted_when_nothing_would_move():
    resume = _resume(bullets=["Built the Python service.", OTHER_BULLET])

    assert [order.item_id for order in derive_bullet_orders(resume, ["Python"])] == []


def test_sanitize_derives_ranking_instead_of_carrying_it_from_the_model():
    resume = _resume(bullets=[OTHER_BULLET, PYTHON_BULLET])
    plan = TailorPlan(
        content_priorities=[],
        bullet_orders=[],
    )

    sanitized = _sanitize(plan, resume, ["Python"])

    assert sanitized.content_priorities
    assert [order.item_id for order in sanitized.bullet_orders] == ["exp_acme"]


# -- call economics ---------------------------------------------------------


class _Recorder:
    def __init__(self):
        self.calls = []

    def generate_core_edit_plan(self, **kwargs):
        from flash_resume.models.tailoring import CoreEditPlan

        self.calls.append("core")
        return CoreEditPlan(summary_edit="Tailored summary")

    def generate_project_edit_plan(self, **kwargs):
        self.calls.append("projects")
        return ProjectEditPlan()

    def generate_tailor_plan(self, **kwargs):
        raise AssertionError("legacy fallback should not be used")


def test_projects_call_is_skipped_when_no_project_bullet_has_a_term_to_add():
    resume = _resume(project_bullets=["Built the Python cache."])
    assessment, targets = _targets(resume, ["Python"])
    llm = _Recorder()
    funnel = EditFunnel()

    generate_split_plan(
        llm, resume, "jd", None, None, ["Python"], [], JDKeywords(required_keywords=["Python"]),
        False, targets=targets, funnel=funnel,
    )

    assert llm.calls == ["core"]
    assert funnel.projects_call_skipped is True


def test_projects_call_runs_when_a_project_bullet_can_absorb_a_term():
    resume = _resume(project_bullets=["Built a cache."])
    _, targets = _targets(resume, ["Python"])
    llm = _Recorder()
    funnel = EditFunnel()

    generate_split_plan(
        llm, resume, "jd", None, None, ["Python"], [], JDKeywords(required_keywords=["Python"]),
        False, targets=targets, funnel=funnel,
    )

    assert llm.calls == ["core", "projects"]
    assert funnel.projects_call_skipped is False


def test_without_a_manifest_both_specialists_are_still_asked():
    """Direct callers get the old behaviour, which keeps the split-plan runner
    honest about the fact that the manifest is what makes the skip possible."""
    resume = _resume(project_bullets=["Built the Python cache."])
    llm = _Recorder()

    generate_split_plan(
        llm, resume, "jd", None, None, ["Python"], [], JDKeywords(required_keywords=["Python"]), False
    )

    assert llm.calls == ["core", "projects"]


def test_report_shows_the_funnel_and_the_ordering():
    resume = _resume(bullets=[OTHER_BULLET, PYTHON_BULLET])
    funnel = EditFunnel(applied=1, editable_targets=2, locked_targets=1, proposed=3)
    funnel.count("facts_dropped")
    funnel.count("facts_dropped")
    plan = TailorPlan(
        bullet_orders=derive_bullet_orders(resume, ["Python"]),
        content_priorities=derive_content_priorities(resume, ["Python"]),
    )

    report = generate_diff_markdown(resume, resume, plan, 1, 12.0, funnel)

    assert "## Bullet Edit Funnel" in report
    assert "3 proposed, 1 applied, 2 rejected, 0 skipped" in report
    assert "Bullets with a JD term left to add:** 2" in report
    assert "| a metric or outcome verb was dropped | 2 |" in report
    assert "## Bullet Ordering" in report
    assert "exp_acme" in report
