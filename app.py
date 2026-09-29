"""Streamlit side-by-side incident response memory demo."""

from concurrent.futures import ThreadPoolExecutor

import streamlit as st

from agent import suggest_fix, suggest_fix_with_memories
from memory import memory_count, retain_outcome
from seed_data import TEST_ALERTS


SEEDED_INCIDENT_COUNT = 15
_MEMORY_COUNT_EXECUTOR = ThreadPoolExecutor(max_workers=1)


def _memory_count_without_blocking():
    if "memory_count_value" in st.session_state:
        return st.session_state["memory_count_value"]
    future = st.session_state.get("memory_count_future")
    if future is None:
        st.session_state["memory_count_future"] = _MEMORY_COUNT_EXECUTOR.submit(memory_count)
        return None
    if not future.done():
        return None
    try:
        value = future.result()
    except Exception:
        value = None
    st.session_state["memory_count_value"] = value
    st.session_state.pop("memory_count_future", None)
    return value


def _safe_text(item):
    score = item.get("relevance_score")
    rank = item.get("relevance_rank", "?")
    count = item.get("recall_count", "?")
    relevance = f"Rank {rank} of {count} recalled incidents"
    if score is not None:
        relevance += f" - Hindsight {item.get('relevance_stage', 'relative')} score {score:.3f} (relative)"
    return relevance


def _load_sample_alert():
    label = st.session_state.get("sample_choice")
    sample = next((item["alert"] for item in TEST_ALERTS if item["label"] == label), "")
    st.session_state["alert_input"] = sample


st.set_page_config(page_title="Incident Response Agent", page_icon="🚨", layout="wide")
st.title("Incident Response Agent")
st.caption("Compare the same production alert with and without long-term incident memory.")

with st.sidebar:
    st.header("Incident memory")
    count = _memory_count_without_blocking()
    if count is None:
        st.metric("Incidents in memory", "Loading")
    else:
        st.metric("Incidents in memory", f"{count:,}")
    st.caption("New incident outcomes are retained live and can inform future suggestions.")

sample_labels = ["Choose a sample alert..."] + [item["label"] for item in TEST_ALERTS]
st.selectbox("Load a sample test alert", sample_labels, key="sample_choice", on_change=_load_sample_alert)
alert = st.text_area("Production alert", key="alert_input", height=150, placeholder="Paste the alert, key symptoms, and relevant log lines...")
analyze = st.button("Compare suggestions", type="primary", disabled=not alert.strip())

if analyze:
    for key in ("without_memory", "with_memory", "recalled"):
        st.session_state.pop(key, None)
    st.session_state["outcome_saved_for_comparison"] = False
    status = st.empty()
    status.info("Generating suggestion without memory")
    with st.spinner("Generating suggestion without memory..."):
        try:
            st.session_state["without_memory"] = suggest_fix(alert.strip(), use_memory=False)
            def show_progress(message):
                status.info(message)

            with st.spinner("Building the memory-informed suggestion..."):
                with_answer, recalled = suggest_fix_with_memories(alert.strip(), on_progress=show_progress)
            st.session_state["with_memory"] = with_answer
            st.session_state["recalled"] = recalled
            st.session_state["active_alert"] = alert.strip()
            st.session_state["analysis_error"] = None
        except Exception as exc:
            st.session_state["analysis_error"] = str(exc)
            status.error(f"Could not complete the comparison: {exc}")

if st.session_state.get("analysis_error"):
    st.error(f"Could not complete the comparison: {st.session_state['analysis_error']}")

without = st.session_state.get("without_memory")
with_mem = st.session_state.get("with_memory")
if without and with_mem:
    left, right = st.columns(2)
    with left:
        st.subheader("Without memory")
        st.caption("Uses only the alert text")
        st.markdown(f"**Likely root cause**\n\n{without['likely_root_cause']}")
        st.markdown("**Recommended fix steps**")
        for step in without["recommended_fix_steps"]:
            st.markdown(f"- {step}")
        st.markdown("**Fixes to avoid**")
        for fix in without["fixes_to_avoid"]:
            st.markdown(f"- {fix}")
        st.caption(f"Confidence: {without['confidence']}")

    with right:
        st.subheader("With memory")
        st.caption("Uses the alert plus similar past incidents and recorded outcomes")
        st.markdown(f"**Likely root cause**\n\n{with_mem['likely_root_cause']}")
        st.markdown("**Recommended fix steps**")
        for step in with_mem["recommended_fix_steps"]:
            st.markdown(f"- {step}")
        st.markdown("**Fixes to avoid**")
        for fix in with_mem["fixes_to_avoid"]:
            st.markdown(f"- {fix}")
        st.caption(f"Confidence: {with_mem['confidence']}")
        st.markdown("**Evidence from past incidents**")
        evidence = with_mem.get("evidence_from_past_incidents", [])
        if evidence:
            ordered_evidence = sorted(evidence, key=lambda item: not item.get("learned_from_feedback", False))[:3]
            recalled_by_id = {item["id"]: item for item in st.session_state.get("recalled", [])}
            for item in ordered_evidence:
                with st.container(border=True):
                    recalled_item = recalled_by_id.get(item["id"], {})
                    display_title = recalled_item.get("title") or item["id"]
                    display_service = recalled_item.get("service") or item["service"]
                    st.markdown(f"**{display_title} - {item['date']} - {display_service}**")
                    if item.get("learned_from_feedback"):
                        st.caption("Learned from feedback")
                        st.markdown(f"**Fix reported:** {'worked' if item.get('feedback_worked') else 'did not work'}")
                        st.markdown(f"**Saved note:** {item.get('feedback_note') or 'No additional notes provided.'}")
                    st.caption(f"Match: {item['match_strength']} - {item['match_reason']}")
                    st.markdown(f"**Fix that worked:** {item['fix_that_worked']}")
                    st.markdown(f"**Fixes that failed:** {item['fixes_that_failed']}")
                    if item.get("time_to_resolve_minutes") not in (None, "", "Not recorded"):
                        st.markdown(f"**Time to resolve:** {item['time_to_resolve_minutes']} minutes")
        else:
            st.info("No sufficiently relevant past incident was used for this answer.")

        with st.expander("Recalled past incidents"):
            recalled = st.session_state.get("recalled", [])
            if recalled:
                for item in recalled:
                    display_id = item.get("title") or item["id"]
                    st.markdown(f"**{display_id} - {item['date']} - {item['service']}**")
                    st.caption(_safe_text(item))
                    if item.get("learned_from_feedback"):
                        st.caption("Learned from feedback")
                        st.markdown(f"Fix reported: {'worked' if item.get('feedback_worked') else 'did not work'}")
                        st.markdown(f"Saved note: {item.get('feedback_note') or 'No additional notes provided.'}")
                    st.markdown(f"Alert: {item['alert']}")
                    st.markdown(f"Root cause: {item['root_cause']}")
                    st.markdown(f"Fix that worked: {item['fix_that_worked']}")
                    st.markdown(f"Fixes that failed: {item['fixes_that_failed']}")
                    if item.get("time_to_resolve_minutes") not in (None, "", "Not recorded"):
                        st.markdown(f"Time to resolve: {item['time_to_resolve_minutes']} minutes")
                    st.divider()
            else:
                st.info("No relevant incident was selected from Hindsight recall.")

    st.divider()
    st.subheader("Did the suggested fix work?")
    note_column, time_column = st.columns(2)
    with note_column:
        notes = st.text_area("Outcome notes (optional)", key="outcome_notes", placeholder="What action was taken? Include impact, workaround, and resolution details.")
    with time_column:
        time_to_resolve = st.number_input(
            "Time to resolve (minutes)", min_value=1, value=None, step=1,
            key="outcome_time_to_resolve", help="Required before saving feedback.",
        )
    outcome_saved = st.session_state.get("outcome_saved_for_comparison", False)
    feedback_left, feedback_right = st.columns(2)
    with feedback_left:
        worked = st.button("Fix worked", type="primary", key="fix_worked", disabled=outcome_saved)
    with feedback_right:
        failed = st.button("Fix did not work", key="fix_failed", disabled=outcome_saved)
    if worked or failed:
        if time_to_resolve is None:
            st.error("Enter the time to resolve before saving this outcome.")
        else:
            try:
                empty_note_confirmation = (
                    "Engineer confirmed the suggested fix worked (no details added)"
                    if worked else "Engineer confirmed the suggested fix did not work (no details added)"
                )
                duplicate = not retain_outcome(
                    st.session_state["active_alert"],
                    st.session_state["with_memory"],
                    worked=worked,
                    notes=notes,
                    time_to_resolve_minutes=time_to_resolve,
                )
                st.session_state["outcome_saved_for_comparison"] = True
                st.session_state.pop("memory_count_value", None)
                st.session_state.pop("memory_count_future", None)
                if duplicate:
                    st.info("This alert and note already have a saved outcome.")
                elif not (notes or "").strip():
                    st.success(empty_note_confirmation)
                else:
                    st.success("Outcome saved to Hindsight. Try a similar alert again to see what the memory recalls.")
            except Exception as exc:
                st.error(f"Could not save this outcome: {exc}")
