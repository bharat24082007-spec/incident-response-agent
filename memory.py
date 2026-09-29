"""Hindsight memory helpers for incidents and their outcomes."""

import os
import re
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from dotenv import load_dotenv
from hindsight_client import Hindsight

load_dotenv()

BANK_ID = "incidents"


def run_in_thread(fn, *args, **kwargs):
    """Run a synchronous Hindsight operation away from Streamlit's event loop."""
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(fn, *args, **kwargs).result()


def _client():
    missing = [key for key in ("HINDSIGHT_URL", "HINDSIGHT_API_KEY") if not os.getenv(key)]
    if missing:
        raise RuntimeError(f"Missing {', '.join(missing)} in .env. Copy .env.example and fill in the values.")
    return Hindsight(base_url=os.environ["HINDSIGHT_URL"], api_key=os.environ["HINDSIGHT_API_KEY"])


def _get(item, key, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _string_metadata(**values):
    return {str(key): str(value) for key, value in values.items()}


def _source_field(source, name, default="Not recorded"):
    match = re.search(rf"^{re.escape(name)}:\s*(.+)$", source, re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip() if match else default


def _parse_incident(source, metadata, document_id):
    metadata = {str(key): str(value) for key, value in (metadata or {}).items()}
    header = re.search(r"^Incident\s+(.+?)\s+\((\d{4}-\d{2}-\d{2})\)", source, re.MULTILINE)
    incident_id = metadata.get("incident_id") or (header.group(1) if header else document_id) or "unknown"
    incident_date = metadata.get("date") or (header.group(2) if header else "Unknown")
    alert = _source_field(source, "Alert")
    service = metadata.get("service") or _source_field(source, "Service", "Unspecified service")
    title = metadata.get("title") or _source_field(source, "Title", "")
    resolution = metadata.get("time_to_resolve_minutes") or _source_field(source, "Time to resolve", "Not recorded")
    minutes = re.search(r"\d+", resolution)
    feedback = _source_field(source, "Engineer feedback", "")
    outcome_match = re.search(r"suggested fix (worked|did not work)", feedback, re.IGNORECASE)
    record_type = str(metadata.get("record_type") or ("outcome" if feedback else "incident")).lower()
    if record_type == "outcome" and (not title or service == "Unspecified service"):
        inferred_service, inferred_title = _service_and_title(alert)
        service = inferred_service if service == "Unspecified service" else service
        title = title or inferred_title
    worked_value = str(metadata.get("worked", "")).lower()
    feedback_worked = worked_value == "true" if worked_value in {"true", "false"} else (
        outcome_match.group(1).lower() == "worked" if outcome_match else None
    )
    note = _source_field(source, "Outcome notes", "")
    return {
        "id": incident_id,
        "date": incident_date,
        "service": service,
        "title": title,
        "alert": alert,
        "root_cause": _source_field(source, "Root cause"),
        "fix_that_worked": _source_field(source, "Fix that worked"),
        "fixes_that_failed": _source_field(source, "Fixes that failed"),
        "time_to_resolve_minutes": int(minutes.group()) if minutes else resolution,
        "record_type": record_type,
        "learned_from_feedback": record_type == "outcome",
        "feedback_worked": feedback_worked,
        "feedback_note": note,
    }


def ensure_bank():
    def ensure():
        with _client() as client:
            try:
                return client.get_bank_config(bank_id=BANK_ID)
            except Exception as exc:
                if getattr(exc, "status", None) != 404:
                    raise
                return client.create_bank(
                    bank_id=BANK_ID,
                    name="Incident Response Agent",
                    mission="Remember production incidents, their causes, attempted fixes, and verified outcomes to help on-call engineers resolve future incidents.",
                )

    return run_in_thread(ensure)


def _hindsight_call(method, *args, **kwargs):
    def call():
        with _client() as client:
            return getattr(client, method)(*args, **kwargs)

    return run_in_thread(call)


def retain_incident(incident):
    content = "\n".join([
        f"Incident {incident['id']} ({incident['date']})",
        f"Service: {incident['service']}",
        f"Alert: {incident['alert']}",
        f"Error log:\n{incident['error_log']}",
        f"Root cause: {incident['root_cause']}",
        f"Fix that worked: {incident['fix_that_worked']}",
        f"Fixes that failed: {incident['fixes_that_failed']}",
        f"Time to resolve: {incident['time_to_resolve_minutes']} minutes",
    ])
    return _hindsight_call(
        "retain",
        bank_id=BANK_ID,
        content=content,
        context="Northstar Commerce production incident and resolution",
        document_id=incident["id"],
        metadata=_string_metadata(
            incident_id=incident["id"],
            service=incident["service"],
            date=incident["date"],
        ),
    )


def recall_similar(alert_text):
    response = _hindsight_call(
        "recall",
        bank_id=BANK_ID,
        query=alert_text,
        max_tokens=2048,
        budget="high",
        include_chunks=True,
        max_chunk_tokens=8192,
    )
    hits = {}
    chunks = response.chunks or {}
    for rank, result in enumerate(response.results or [], start=1):
        metadata = _get(result, "metadata") or {}
        document_id = _get(result, "document_id")
        chunk = chunks.get(_get(result, "chunk_id")) if _get(result, "chunk_id") else None
        source = _get(chunk, "text") or _get(result, "text", "")
        parsed = _parse_incident(source, metadata, document_id)
        if not parsed["id"] or parsed["id"] == "unknown":
            continue
        scores = _get(result, "scores")
        final_score = _get(scores, "final")
        reranker_score = _get(scores, "reranker")
        score = final_score if final_score is not None else reranker_score
        stage = "final" if final_score is not None else "reranker" if reranker_score is not None else "rank"

        hit = hits.get(parsed["id"])
        if hit is None:
            hit = {
                **parsed,
                "source_text": source,
                "relevance_rank": rank,
                "relevance_score": score,
                "relevance_stage": stage,
            }
            hits[parsed["id"]] = hit
        elif hit.get("relevance_score") is None and score is not None:
            hit.update(relevance_score=score, relevance_stage=stage)

    ordered = sorted(hits.values(), key=lambda hit: hit["relevance_rank"])
    recall_count = len(ordered)
    for rank, hit in enumerate(ordered, start=1):
        hit["relevance_rank"] = rank
        hit["recall_count"] = recall_count
    return ordered


def retain_outcome(alert, suggestion, worked: bool, notes, time_to_resolve_minutes=None):
    note = " ".join((notes or "").split())
    submitted_note = note
    duplicate = _outcome_exists(alert, note)
    if duplicate:
        return False

    outcome_id = "outcome-" + hashlib.sha256(f"{alert.strip()}\n{note}".encode()).hexdigest()[:16]
    root_cause = suggestion.get("likely_root_cause", "Not confirmed") if isinstance(suggestion, dict) else "Not confirmed"
    status = "worked" if worked else "did not work"
    service, title = _service_and_title(alert)
    if not note:
        note = (
            "Engineer confirmed the suggested fix worked (no details added)"
            if worked else "Engineer confirmed the suggested fix did not work (no details added)"
        )
        working_fix = note if worked else "No fix was confirmed to work"
        failed_fix = "None reported" if worked else note
    else:
        working_fix = note if worked else "No fix was confirmed to work"
        failed_fix = "None reported" if worked else note
    time_to_resolve = time_to_resolve_minutes
    time_line = f"Time to resolve: {time_to_resolve} minutes" if time_to_resolve is not None else ""
    content = "\n".join([
        f"Incident {outcome_id} ({date.today().isoformat()})",
        f"Service: {service}",
        f"Title: {title}",
        f"Alert: {alert}",
        f"Root cause: {root_cause}",
        f"Fix that worked: {working_fix}",
        f"Fixes that failed: {failed_fix}",
        time_line,
        f"Engineer feedback: The suggested fix {status}.",
        f"Outcome notes: {note}",
    ])
    return _hindsight_call(
        "retain",
        bank_id=BANK_ID,
        content=content,
        context="Northstar Commerce production incident outcome",
        document_id=outcome_id,
        metadata=_string_metadata(
            incident_id=outcome_id,
            service=service,
            title=title,
            date=date.today().isoformat(),
            record_type="outcome",
            worked="true" if worked else "false",
            note=submitted_note,
            alert=alert.strip(),
            time_to_resolve_minutes=time_to_resolve if time_to_resolve is not None else "",
        ),
    )


def _service_and_title(alert):
    text = " ".join((alert or "").split())
    match = re.search(r"\b(?:on|for|service[=: ])\s+([a-z][a-z0-9-]+)", text, re.IGNORECASE)
    if not match:
        match = re.search(r"\b([a-z][a-z0-9-]*(?:-api|-gateway|-edge|-worker|-projection))\b", text, re.IGNORECASE)
    service = match.group(1) if match else "Unspecified service"
    service = service.strip(".,:;()")
    lowered = text.lower()
    issue = next((name for keyword, name in (
        ("schema", "schema mismatch"), ("redis", "Redis connection saturation"),
        ("postgres", "database connection exhaustion"), ("database", "database failure"),
        ("kafka", "Kafka consumer failure"), ("lag", "consumer lag"),
        ("502", "upstream 502 errors"), ("dns", "DNS resolution failure"),
        ("certificate", "certificate expiry"), ("memory", "memory growth"),
    ) if keyword in lowered), None)
    if not issue:
        issue = " ".join(word.strip(".,:;()") for word in text.split()[:5]) or "production alert"
    return service, f"{service} - {issue}"


def _outcome_exists(alert, note):
    """Check stored outcome metadata so repeated submissions are skipped across reruns."""
    result = _hindsight_call(
        "list_memories", bank_id=BANK_ID, search_query=alert, limit=1000, offset=0
    )
    records = result if isinstance(result, list) else _get(result, "items", [])
    alert_key = " ".join((alert or "").split()).casefold()
    note_key = " ".join((note or "").split()).casefold()
    for item in records or []:
        metadata = _get(item, "metadata") or {}
        if str(_get(metadata, "record_type", "")) != "outcome":
            continue
        saved_alert = " ".join(str(_get(metadata, "alert", "")).split()).casefold()
        saved_note = " ".join(str(_get(metadata, "note", "")).split()).casefold()
        if saved_alert == alert_key and saved_note == note_key:
            return True
    return False


def reset_bank():
    """Delete the incidents bank; the reset script recreates it with seed data."""
    try:
        return _hindsight_call("delete_bank", bank_id=BANK_ID)
    except Exception as exc:
        if getattr(exc, "status", None) == 404:
            return None
        raise


def memory_count():
    """Return the number of distinct incidents represented in listed memories."""
    result = _hindsight_call("list_memories", bank_id=BANK_ID, limit=1000, offset=0)
    records = result if isinstance(result, list) else None
    for key in ("memories", "results", "items"):
        value = _get(result, key)
        if isinstance(value, list):
            records = value
            break
    if records is None:
        return None
    incident_ids = {
        (_get(_get(item, "metadata") or {}, "incident_id") or _get(item, "document_id"))
        for item in records
    }
    return len({incident_id for incident_id in incident_ids if incident_id})
