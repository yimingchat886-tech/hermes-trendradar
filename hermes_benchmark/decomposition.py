"""Hermes benchmark decomposition output boundary."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any, Literal, TypedDict

from .contracts import BenchmarkContent, ContractError, EvidenceState, Transcript

RUN_ID = "run-child5-hermes-decomposition-2026-07-01"
OBSERVED_AT = "2026-07-01T00:00:00-07:00"

TopicSupplementMode = Literal["candidate_suggestion", "manual_topic_supplement"]

EVIDENCE_STATES = {"sufficient", "insufficient", "placeholder"}
SUPPLEMENT_MODES = {"candidate_suggestion", "manual_topic_supplement"}
MANUAL_FIELDS = frozenset(
    {
        "official_topic_title",
        "selected_topic_id",
        "manual_card_title",
        "manual_evidence_override",
    }
)
FORBIDDEN_AUTONOMOUS_FIELDS = frozenset({"official_topic_title"})

OUTPUT_REQUIRED = (
    "id",
    "source_id",
    "content_id",
    "transcript_id",
    "trace_id",
    "summary",
    "topic_one_liner",
    "content_type",
    "hook",
    "title_formula",
    "structure",
    "audience_pain",
    "reusable_angle",
    "non_reusable_notes",
    "evidence_state",
    "sedimentation_suggestion",
    "card",
    "topic_pool_supplement",
    "trace",
)
EXPORT_REF_FIELDS = ("source_id", "content_id", "transcript_id", "trace_id", "evidence_state")
EXPORT_REQUIRED = ("id",) + EXPORT_REF_FIELDS


class HermesCard(TypedDict, total=False):
    id: str
    source_id: str
    content_id: str
    transcript_id: str
    trace_id: str
    headline: str
    hook: str
    summary: str
    evidence_state: EvidenceState
    source_url: str


class TopicPoolSupplement(TypedDict, total=False):
    id: str
    source_id: str
    content_id: str
    transcript_id: str
    trace_id: str
    mode: TopicSupplementMode
    candidate_only: bool
    candidate_label: str
    suggested_angle: str
    rationale: str
    manual_topic_title: str
    evidence_state: EvidenceState


class HermesDecompositionOutput(TypedDict, total=False):
    id: str
    source_id: str
    content_id: str
    transcript_id: str
    trace_id: str
    summary: str
    topic_one_liner: str
    content_type: str
    hook: str
    title_formula: str
    structure: str
    audience_pain: str
    reusable_angle: str
    non_reusable_notes: str
    evidence_state: EvidenceState
    sedimentation_suggestion: str
    card: HermesCard
    topic_pool_supplement: TopicPoolSupplement
    trace: dict[str, str]


def mock_hermes_output(
    content: BenchmarkContent,
    transcript: Transcript,
    *,
    manual_topic_title: str = "",
) -> HermesDecompositionOutput:
    evidence_state = _evidence_state(content, transcript)
    output_id = f"hermes-{content['id'].removeprefix('content-')}"
    transcript_id = transcript["id"]
    trace_id = f"{RUN_ID}:{output_id}"
    summary = f"{content['title']} benchmark summary."
    hook = "Start from the benchmark move, then name the repeatable angle."
    output: HermesDecompositionOutput = {
        "id": output_id,
        "source_id": content["source_id"],
        "content_id": content["id"],
        "transcript_id": transcript_id,
        "trace_id": trace_id,
        "summary": summary,
        "topic_one_liner": f"Reuse the core move from {content['title']}.",
        "content_type": "benchmark_video",
        "hook": hook,
        "title_formula": "Observed move + audience pain + proof point",
        "structure": "hook -> evidence -> reusable angle -> caveat",
        "audience_pain": "Hard to tell which benchmark move is repeatable.",
        "reusable_angle": "Extract the opening hook and proof sequence.",
        "non_reusable_notes": "Do not copy creator identity, exact title, or private platform context.",
        "evidence_state": evidence_state,
        "sedimentation_suggestion": "Save as benchmark-derived angle after manual review.",
        "card": {
            "id": f"card-{content['id'].removeprefix('content-')}",
            "source_id": content["source_id"],
            "content_id": content["id"],
            "transcript_id": transcript_id,
            "trace_id": trace_id,
            "headline": content["title"],
            "hook": hook,
            "summary": summary,
            "evidence_state": evidence_state,
            "source_url": content.get("raw_source_url") or content.get("url", ""),
        },
        "topic_pool_supplement": _topic_pool_supplement(content, transcript_id, trace_id, evidence_state, manual_topic_title),
        "trace": _trace(output_id, content),
    }
    validate_hermes_output(output, content, transcript)
    return output


def validate_hermes_output(
    output: Mapping[str, Any],
    content: Mapping[str, Any] | None = None,
    transcript: Mapping[str, Any] | None = None,
) -> None:
    _reject_forbidden_fields(output)
    _require_fields("hermes_output", output, OUTPUT_REQUIRED)
    _validate_trace("hermes_output", output)

    evidence_state = output["evidence_state"]
    if evidence_state not in EVIDENCE_STATES:
        raise ContractError(f"unsupported Hermes evidence_state: {evidence_state}")

    if content is not None:
        if output["content_id"] != content["id"]:
            raise ContractError("Hermes output content_id must match content")
        if output["source_id"] != content["source_id"]:
            raise ContractError("Hermes output source_id must match content")
        if content.get("evidence_state") == "insufficient" and evidence_state != "insufficient":
            raise ContractError("evidence-insufficient content must remain marked")

    if transcript is not None:
        if output["transcript_id"] != transcript["id"]:
            raise ContractError("Hermes output transcript_id must match transcript")
        if transcript.get("content_id") != output["content_id"]:
            raise ContractError("Hermes output transcript must belong to content")
        if transcript.get("status") != "done" and evidence_state != "insufficient":
            raise ContractError("non-done transcript must keep output evidence insufficient")

    _validate_card(output["card"], output)
    _validate_topic_pool_supplement(output["topic_pool_supplement"], output)


def merge_manual_fields(manual_fields: Mapping[str, Any], hermes_fields: Mapping[str, Any]) -> dict[str, Any]:
    overwritten = sorted(MANUAL_FIELDS.intersection(hermes_fields))
    if overwritten:
        raise ContractError(f"Hermes output cannot overwrite manual fields: {overwritten}")
    merged = dict(hermes_fields)
    merged.update(manual_fields)
    return merged


def _topic_pool_supplement(
    content: BenchmarkContent,
    transcript_id: str,
    trace_id: str,
    evidence_state: EvidenceState,
    manual_topic_title: str,
) -> TopicPoolSupplement:
    base: TopicPoolSupplement = {
        "id": f"topic-supplement-{content['id'].removeprefix('content-')}",
        "source_id": content["source_id"],
        "content_id": content["id"],
        "transcript_id": transcript_id,
        "trace_id": trace_id,
        "evidence_state": evidence_state,
    }
    if manual_topic_title:
        base.update(
            {
                "mode": "manual_topic_supplement",
                "candidate_only": False,
                "manual_topic_title": manual_topic_title,
                "suggested_angle": "Attach benchmark evidence to the existing manual topic.",
                "rationale": "Manual topic exists; Hermes only adds source-backed supplement fields.",
            }
        )
    else:
        base.update(
            {
                "mode": "candidate_suggestion",
                "candidate_only": True,
                "candidate_label": "benchmark-derived candidate",
                "suggested_angle": "Candidate angle for manual topic-pool review.",
                "rationale": "No manual topic title exists, so this remains suggestion-shaped.",
            }
        )
    return base


def _validate_card(card: Any, output: Mapping[str, Any]) -> None:
    if not isinstance(card, Mapping):
        raise ContractError("Hermes card must be an object")
    _require_fields("Hermes card", card, EXPORT_REQUIRED + ("headline", "hook", "summary", "source_url"))
    _validate_export_refs("Hermes card", card, output)


def _validate_topic_pool_supplement(supplement: Any, output: Mapping[str, Any]) -> None:
    if not isinstance(supplement, Mapping):
        raise ContractError("topic_pool_supplement must be an object")
    _require_fields(
        "topic_pool_supplement",
        supplement,
        EXPORT_REQUIRED + ("mode", "candidate_only", "suggested_angle", "rationale"),
    )
    _validate_export_refs("topic_pool_supplement", supplement, output)
    if supplement["mode"] not in SUPPLEMENT_MODES:
        raise ContractError(f"unsupported topic supplement mode: {supplement['mode']}")
    if supplement["mode"] == "candidate_suggestion":
        if not supplement["candidate_only"]:
            raise ContractError("candidate_suggestion must remain candidate_only")
        if "manual_topic_title" in supplement:
            raise ContractError("candidate_suggestion must not include manual_topic_title")
    if supplement["mode"] == "manual_topic_supplement":
        if supplement["candidate_only"]:
            raise ContractError("manual_topic_supplement cannot be candidate_only")
        if not supplement.get("manual_topic_title"):
            raise ContractError("manual_topic_supplement requires manual_topic_title")


def _validate_export_refs(label: str, record: Mapping[str, Any], output: Mapping[str, Any]) -> None:
    for field in EXPORT_REF_FIELDS:
        if record[field] != output[field]:
            raise ContractError(f"{label} {field} must match Hermes output")


def _validate_trace(label: str, record: Mapping[str, Any]) -> None:
    trace = record["trace"]
    if not isinstance(trace, Mapping):
        raise ContractError(f"{label} trace must be an object")
    if trace.get("local_id") != record["id"]:
        raise ContractError(f"{label} trace.local_id must match id")
    if trace.get("run_id") != RUN_ID:
        raise ContractError(f"{label} trace.run_id must match child 5 run")
    if not trace.get("observed_at"):
        raise ContractError(f"{label} missing trace.observed_at")


def _require_fields(label: str, record: Mapping[str, Any], fields: tuple[str, ...]) -> None:
    missing = [field for field in fields if field not in record]
    if missing:
        raise ContractError(f"{label} missing: {missing}")


def _reject_forbidden_fields(value: Any, path: str = "") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if key in FORBIDDEN_AUTONOMOUS_FIELDS:
                raise ContractError(f"Hermes output cannot set autonomous field: {path}{key}")
            _reject_forbidden_fields(nested, f"{path}{key}.")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_forbidden_fields(nested, f"{path}{index}.")


def _evidence_state(content: Mapping[str, Any], transcript: Mapping[str, Any]) -> EvidenceState:
    if content.get("evidence_state") == "insufficient" or transcript.get("status") != "done":
        return "insufficient"
    return "sufficient"


def _trace(local_id: str, content: Mapping[str, Any]) -> dict[str, str]:
    source_url = content.get("raw_source_url") or content.get("url", "")
    return {
        "local_id": local_id,
        "run_id": RUN_ID,
        "observed_at": OBSERVED_AT,
        "source_id": content["source_id"],
        "source_url": source_url,
        "content_id": content["id"],
    }


def _self_check() -> None:
    from .mediacrawler_import import import_mediacrawler_rows, load_mediacrawler_fixture
    from .transcript_pipeline import transcript_fixture

    contents = import_mediacrawler_rows(load_mediacrawler_fixture())["contents"]
    content = contents[0]
    transcript = transcript_fixture(content)

    candidate = mock_hermes_output(content, transcript)
    assert candidate["topic_pool_supplement"]["mode"] == "candidate_suggestion"
    assert candidate["topic_pool_supplement"]["candidate_only"] is True

    manual = mock_hermes_output(content, transcript, manual_topic_title="Manual topic title")
    assert manual["topic_pool_supplement"]["mode"] == "manual_topic_supplement"
    assert manual["topic_pool_supplement"]["manual_topic_title"] == "Manual topic title"

    insufficient = mock_hermes_output(contents[1], transcript_fixture(contents[1]))
    assert insufficient["evidence_state"] == "insufficient"
    assert insufficient["card"]["evidence_state"] == "insufficient"
    assert insufficient["topic_pool_supplement"]["evidence_state"] == "insufficient"

    merged = merge_manual_fields({"manual_card_title": "Keep this"}, {"summary": "Generated summary"})
    assert merged["manual_card_title"] == "Keep this"
    _expect_contract_error(
        "cannot overwrite manual fields",
        lambda: merge_manual_fields({"manual_card_title": "Keep"}, {"manual_card_title": "Overwrite"}),
    )

    bad = deepcopy(candidate)
    bad["official_topic_title"] = "Autonomous title"
    _expect_contract_error("cannot set autonomous field", lambda: validate_hermes_output(bad, content, transcript))

    bad = deepcopy(candidate)
    del bad["card"]["trace_id"]
    _expect_contract_error("Hermes card missing", lambda: validate_hermes_output(bad, content, transcript))


def _expect_contract_error(fragment: str, action: Any) -> None:
    try:
        action()
    except ContractError as exc:
        if fragment not in str(exc):
            raise AssertionError(f"expected {fragment!r} in {exc!r}") from exc
    else:
        raise AssertionError(f"expected ContractError containing {fragment!r}")


if __name__ == "__main__":
    _self_check()
    print("Hermes decomposition outputs ok")
