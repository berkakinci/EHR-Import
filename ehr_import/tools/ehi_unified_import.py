"""
EHI Unified Import — Dual-Output Pipeline.

Imports an Epic EHI (Electronic Health Information) export into two databases:

1. **Raw DB** (ehi_raw.db) — ALL tables from the export, losslessly preserved.
   This is a complete archive (same behavior as the original ehi_import.py).
2. **Unified DB** (ehr_data.db) — Clinically mappable tables extracted and
   normalized into the shared schema used by FHIR pulls and C-CDA imports.

Mappable tables: ORDER_RESULTS → labs, ALLERGY → allergies, ORDER_MED → medications,
PROBLEM_LIST → conditions, V_EHI_FLO_MEAS_VALUE → vitals, HNO_PLAIN_TEXT → notes,
PAT_ENC → encounters, IMMUNE → immunizations, IB_MESSAGES → messages,
FAMILY_HX → family_history, SOCIAL_HX → social_history.

Received C-CDAs are delegated to ccda_import for parsing.

Usage:
    python ehi_import.py --source /path/to/Extracted --db ./ehr_data.db
"""

import re
import sqlite3
import sys
import time
from pathlib import Path
from datetime import datetime

from .ehi_import import build_database as build_raw_database
from .ccda_import import (
    build_database as build_ccda_database,
    detect_patient_id as ccda_detect_patient_id,
)

SOURCE_TAG = "ehi_epic"


# --- Date parsing ---

def parse_epic_date(date_str):
    """Parse Epic date format (M/D/YYYY H:MM:SS AM/PM) to ISO date."""
    if not date_str or not date_str.strip():
        return None
    date_str = date_str.strip()
    try:
        # "1/8/2025 12:00:00 AM" or "1/8/2025 8:15:00 AM"
        dt = datetime.strptime(date_str, "%m/%d/%Y %I:%M:%S %p")
        # If time is midnight, return date-only
        if dt.hour == 0 and dt.minute == 0 and dt.second == 0:
            return dt.strftime("%Y-%m-%d")
        return dt.strftime("%Y-%m-%dT%H:%M:%S")
    except ValueError:
        pass
    try:
        # Date-only format
        dt = datetime.strptime(date_str, "%m/%d/%Y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        pass
    return date_str


# --- RTF → plain text extraction ---

def rtf_to_text(rtf_bytes):
    """Extract plain text from RTF content using striprtf."""
    if not rtf_bytes:
        return ""
    try:
        rtf_str = rtf_bytes.decode("utf-8", errors="replace")
    except (AttributeError, UnicodeDecodeError):
        rtf_str = str(rtf_bytes)

    if not rtf_str.startswith("{\\rtf"):
        return rtf_str

    from striprtf.striprtf import rtf_to_text as _striprtf
    text = _striprtf(rtf_str)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text


# --- Patient detection ---

def detect_patient_from_ehi(raw_db_path, unified_db_path):
    """Auto-detect patient_id by matching PATIENT table against unified DB.

    Returns (patient_id, patient_name) or (None, None) if no match.
    """
    if not raw_db_path.exists():
        return None, None

    raw_conn = sqlite3.connect(str(raw_db_path))
    row = raw_conn.execute(
        "SELECT PAT_NAME FROM PATIENT LIMIT 1"
    ).fetchone()
    raw_conn.close()

    if not row or not row[0]:
        return None, None

    pat_name = row[0]  # Format: "DEWILDE,KIARA" or "LAST,FIRST"
    parts = pat_name.split(",")
    if len(parts) < 2:
        print(f"  Could not parse patient name: {pat_name}")
        return None, None

    family_name = parts[0].strip()
    given_name = parts[1].strip()
    print(f"  EHI patient: {given_name} {family_name}")

    if not unified_db_path.exists():
        print(f"  ERROR: Unified DB {unified_db_path} does not exist.")
        print(f"  Use --patient-id to specify manually.")
        return None, None

    conn = sqlite3.connect(str(unified_db_path))
    rows = conn.execute(
        "SELECT patient_id, given_name, family_name, provider FROM patients "
        "WHERE LOWER(family_name) = LOWER(?) "
        "AND LOWER(SUBSTR(given_name, 1, ?)) = LOWER(SUBSTR(?, 1, ?))",
        (family_name, len(given_name), given_name, len(given_name))
    ).fetchall()
    conn.close()

    if len(rows) == 0:
        print(f"  ERROR: No patient match for {given_name} {family_name} in unified DB.")
        print(f"  Use --patient-id to specify manually.")
        return None, None
    elif len(rows) == 1:
        pid = rows[0][0]
        print(f"  Matched: {rows[0][1]} {rows[0][2]} ({rows[0][3]}) → {pid}")
        return pid, f"{rows[0][1]} {rows[0][2]}"
    else:
        # Multiple matches — pick the one from the provider matching EHI source
        print(f"  Multiple patient records match:")
        for pid, given, fam, prov in rows:
            print(f"    {given} {fam} ({prov}) → {pid}")
        selected = rows[0][0]
        print(f"  Using: {selected}")
        return selected, f"{rows[0][1]} {rows[0][2]}"


# --- Provider detection ---

def detect_provider_from_ehi(raw_db_path):
    """Try to detect the provider name from EHI metadata or data."""
    if not raw_db_path.exists():
        return None

    conn = sqlite3.connect(str(raw_db_path))

    # Check _ehi_metadata for source_dir (might contain provider name)
    try:
        row = conn.execute(
            "SELECT value FROM _ehi_metadata WHERE key='source_dir'"
        ).fetchone()
        if row:
            # Extract from path like ".../Boston Childrens - Corvid..."
            source_path = row[0]
            # Common patterns
            if "Boston Children" in source_path:
                conn.close()
                return "Boston Children's"
    except Exception:
        pass

    conn.close()
    return None


# --- Table mappers ---

def insert_rows(conn, table, rows):
    """Insert rows with INSERT OR IGNORE for idempotent dedup."""
    if not rows:
        return 0
    columns = list(rows[0].keys())
    placeholders = ",".join("?" * len(columns))
    col_str = ",".join(columns)
    sql = f"INSERT OR IGNORE INTO {table} ({col_str}) VALUES ({placeholders})"

    inserted = 0
    for row in rows:
        values = [row.get(c) for c in columns]
        cur = conn.execute(sql, values)
        inserted += cur.rowcount

    conn.commit()
    return inserted


def map_labs(raw_conn, patient_id, provider):
    """Map ORDER_RESULTS → labs. Join ORDER_PROC for panel name, CLARITY_SER for provider."""
    rows = []

    # Build lookup: ORDER_PROC_ID → (DESCRIPTION, AUTHRZING_PROV_ID)
    order_proc = {}
    try:
        for r in raw_conn.execute(
            "SELECT ORDER_PROC_ID, DESCRIPTION, AUTHRZING_PROV_ID FROM ORDER_PROC"
        ).fetchall():
            order_proc[r[0]] = (r[1], r[2])
    except Exception:
        pass

    # Build lookup: PROV_ID → name
    providers = {}
    try:
        for r in raw_conn.execute(
            "SELECT PROV_ID, PROV_NAME FROM CLARITY_SER"
        ).fetchall():
            providers[r[0]] = r[1]
    except Exception:
        pass

    for r in raw_conn.execute("SELECT * FROM ORDER_RESULTS").fetchall():
        order_proc_id = r[0]
        line = r[1]
        result_date = parse_epic_date(r[4])  # RESULT_DATE
        component_name = r[5]  # COMPONENT_ID_NAME
        ord_value = r[8]  # ORD_VALUE
        ref_low = r[11]  # REFERENCE_LOW
        ref_high = r[12]  # REFERENCE_HIGH
        ref_unit = r[13]  # REFERENCE_UNIT
        status = r[14]  # RESULT_STATUS_C_NAME

        if not result_date or not ord_value:
            continue

        # Reference range
        ref_range = ""
        if ref_low and ref_high:
            ref_range = f"{ref_low}-{ref_high}"
        elif r[22]:  # REF_NORMAL_VALS
            ref_range = r[22]

        # Panel name and ordering provider from ORDER_PROC join
        panel_name = None
        ordering_provider = None
        if order_proc_id in order_proc:
            panel_name = order_proc[order_proc_id][0]
            prov_id = order_proc[order_proc_id][1]
            if prov_id and prov_id in providers:
                ordering_provider = providers[prov_id]

        fhir_id = f"ehi:{order_proc_id}:{line}"

        rows.append({
            "fhir_id": fhir_id,
            "patient_id": patient_id,
            "provider": provider,
            "code_display": component_name or "",
            "value": ord_value,
            "unit": ref_unit or "",
            "reference_range": ref_range,
            "status": (status or "final").lower(),
            "ordering_provider": ordering_provider,
            "panel_name": panel_name,
            "effective_date": result_date,
            "raw_json": None,
            "source": SOURCE_TAG,
        })

    return rows


def map_allergies(raw_conn, patient_id, provider):
    """Map ALLERGY → allergies."""
    rows = []
    for r in raw_conn.execute("SELECT * FROM ALLERGY").fetchall():
        allergy_id = r[0]
        allergen_name = r[1]  # ALLERGEN_ID_ALLERGEN_NAME
        reaction = r[3]  # REACTION
        date_noted = parse_epic_date(r[4])  # DATE_NOTED
        severity = r[7]  # SEVERITY_C_NAME
        allergy_severity = r[8]  # ALLERGY_SEVERITY_C_NAME
        status = r[9]  # ALRGY_STATUS_C_NAME

        if not allergen_name:
            continue

        fhir_id = f"ehi:allergy:{allergy_id}"

        rows.append({
            "fhir_id": fhir_id,
            "patient_id": patient_id,
            "provider": provider,
            "code_display": allergen_name,
            "clinical_status": (status or "active").lower(),
            "verification_status": "confirmed",
            "type": None,
            "category": None,
            "criticality": (allergy_severity or severity or "").lower() or None,
            "onset_date": date_noted,
            "recorded_date": date_noted,
            "reaction_text": reaction or "",
            "effective_date": date_noted,
            "raw_json": None,
            "source": SOURCE_TAG,
        })

    return rows


def map_medications(raw_conn, patient_id, provider):
    """Map ORDER_MED → medications."""
    rows = []
    for r in raw_conn.execute(
        "SELECT ORDER_MED_ID, DISPLAY_NAME, ORDER_STATUS_C_NAME, START_DATE, "
        "END_DATE, HV_DISCRETE_DOSE, HV_DOSE_UNIT_C_NAME, MED_ROUTE_C_NAME, "
        "AMB_MED_DISP_NAME FROM ORDER_MED"
    ).fetchall():
        order_med_id = r[0]
        display_name = r[1] or r[8]  # DISPLAY_NAME or AMB_MED_DISP_NAME
        status = r[2]  # ORDER_STATUS_C_NAME
        start_date = parse_epic_date(r[3])
        end_date = parse_epic_date(r[4])
        dose = r[5]  # HV_DISCRETE_DOSE
        dose_unit = r[6]  # HV_DOSE_UNIT_C_NAME
        route = r[7]  # MED_ROUTE_C_NAME

        if not display_name:
            continue

        dosage_text = ""
        if dose and dose_unit:
            dosage_text = f"{dose} {dose_unit}"
            if route:
                dosage_text += f" {route}"

        fhir_id = f"ehi:med:{order_med_id}"

        rows.append({
            "fhir_id": fhir_id,
            "patient_id": patient_id,
            "provider": provider,
            "medication_name": display_name,
            "status": (status or "").lower() or "active",
            "intent": "order",
            "reported": 0,
            "authored_on": start_date,
            "dosage_text": dosage_text,
            "requester": "",
            "effective_date": start_date,
            "raw_json": None,
            "source": SOURCE_TAG,
        })

    return rows


def map_conditions(raw_conn, patient_id, provider):
    """Map PROBLEM_LIST → conditions. Join CLARITY_EDG for display name."""
    rows = []

    # Build DX_ID → display name lookup
    dx_names = {}
    try:
        for r in raw_conn.execute("SELECT DX_ID, DX_NAME FROM CLARITY_EDG").fetchall():
            dx_names[r[0]] = r[1]
    except Exception:
        pass

    for r in raw_conn.execute("SELECT * FROM PROBLEM_LIST").fetchall():
        problem_id = r[0]
        dx_id = r[1]
        description = r[2]  # DESCRIPTION
        noted_date = parse_epic_date(r[3])  # NOTED_DATE
        resolved_date = parse_epic_date(r[4])  # RESOLVED_DATE
        problem_status = r[11]  # PROBLEM_STATUS_C_NAME

        # Prefer CLARITY_EDG name, fall back to DESCRIPTION
        display = dx_names.get(dx_id, description) or description
        if not display:
            continue

        clinical_status = "active"
        if problem_status:
            ps = problem_status.lower()
            if "resolved" in ps:
                clinical_status = "resolved"
            elif "inactive" in ps:
                clinical_status = "inactive"

        fhir_id = f"ehi:problem:{problem_id}"

        rows.append({
            "fhir_id": fhir_id,
            "patient_id": patient_id,
            "provider": provider,
            "code_display": display,
            "clinical_status": clinical_status,
            "verification_status": "confirmed",
            "category": "Problem List Item",
            "onset_date": noted_date,
            "abatement_date": resolved_date,
            "effective_date": noted_date,
            "raw_json": None,
            "source": SOURCE_TAG,
        })

    return rows


def map_vitals(raw_conn, patient_id, provider):
    """Map V_EHI_FLO_MEAS_VALUE + IP_FLWSHT_MEAS → vitals.

    Filter to real vitals: BP, Pulse/Heart Rate, Temp, Height, Weight, SpO2, BMI, Resp.
    Uses IP_FLWSHT_MEAS for timestamps (RECORDED_TIME).
    """
    rows = []

    # Vital sign display names to include (case-insensitive partial match)
    VITAL_NAMES = {
        "bp", "blood pressure", "pulse", "heart rate", "temp", "temperature",
        "height", "weight", "spo2", "bmi", "resp", "respiratory rate",
    }

    # Build FSD_ID:LINE → RECORDED_TIME lookup from IP_FLWSHT_MEAS
    timestamps = {}
    try:
        for r in raw_conn.execute(
            "SELECT FSD_ID, LINE, RECORDED_TIME FROM IP_FLWSHT_MEAS"
        ).fetchall():
            timestamps[(r[0], r[1])] = r[2]
    except Exception:
        pass

    for r in raw_conn.execute("SELECT * FROM V_EHI_FLO_MEAS_VALUE").fetchall():
        fsd_id = r[0]
        line = r[1]
        value = r[2]  # MEAS_VALUE_EXTERNAL
        unit = r[3]  # UNITS
        disp_name = r[5]  # FLO_MEAS_ID_DISP_NAME

        if not value or not disp_name:
            continue

        # Filter to real vitals
        name_lower = disp_name.lower()
        if not any(v in name_lower for v in VITAL_NAMES):
            continue

        # Get timestamp from IP_FLWSHT_MEAS
        recorded_time = timestamps.get((fsd_id, line))
        date = parse_epic_date(recorded_time) if recorded_time else None
        if not date:
            continue

        fhir_id = f"ehi:vital:{fsd_id}:{line}"

        rows.append({
            "fhir_id": fhir_id,
            "patient_id": patient_id,
            "provider": provider,
            "code_display": disp_name,
            "value": value,
            "unit": unit or "",
            "status": "final",
            "effective_date": date,
            "raw_json": None,
            "source": SOURCE_TAG,
        })

    return rows


def map_encounters(raw_conn, patient_id, provider):
    """Map PAT_ENC → encounters.

    Uses CALCULATED_ENC_STAT_C_NAME = 'Complete' (not APPT_STATUS) because many
    clinical encounters (phone, results review, messaging) have no appointment status
    but are marked Complete in the calculated field.
    Excludes 'Invalid' status encounters.
    """
    rows = []

    # Provider name lookup
    providers = {}
    try:
        for r in raw_conn.execute(
            "SELECT PROV_ID, PROV_NAME FROM CLARITY_SER"
        ).fetchall():
            providers[r[0]] = r[1]
    except Exception:
        pass

    # Encounter diagnoses lookup (first primary DX per encounter)
    enc_dx = {}
    try:
        # Build DX_ID → name
        dx_names = {}
        for r in raw_conn.execute("SELECT DX_ID, DX_NAME FROM CLARITY_EDG").fetchall():
            dx_names[r[0]] = r[1]

        for r in raw_conn.execute(
            "SELECT PAT_ENC_CSN_ID, DX_ID, PRIMARY_DX_YN FROM PAT_ENC_DX ORDER BY LINE"
        ).fetchall():
            csn = r[0]
            dx_id = r[1]
            if csn not in enc_dx and dx_id in dx_names:
                enc_dx[csn] = dx_names[dx_id]
    except Exception:
        pass

    for r in raw_conn.execute(
        "SELECT PAT_ENC_CSN_ID, CONTACT_DATE, VISIT_PROV_ID, "
        "APPT_STATUS_C_NAME, DEPARTMENT_ID, CALCULATED_ENC_STAT_C_NAME "
        "FROM PAT_ENC "
        "WHERE CALCULATED_ENC_STAT_C_NAME = 'Complete'"
    ).fetchall():
        csn_id = r[0]
        contact_date = parse_epic_date(r[1])
        visit_prov_id = r[2]
        appt_status = r[3]
        dept_id = r[4]

        if not contact_date:
            continue

        participant = providers.get(visit_prov_id, "")
        # Skip generic/historical providers — these are system-generated encounters
        if participant in ("GENERIC EXTERNAL DATA PROVIDER", "PROVIDER, HISTORICAL",
                          "HISTORIC PROVIDER"):
            continue

        # Determine encounter class from APPT_STATUS presence
        enc_class = "ambulatory" if appt_status == "Completed" else "virtual"
        reason = enc_dx.get(csn_id, "")

        fhir_id = f"ehi:enc:{csn_id}"

        rows.append({
            "fhir_id": fhir_id,
            "patient_id": patient_id,
            "provider": provider,
            "encounter_type": "office visit" if appt_status else "other",
            "status": "finished",
            "class": enc_class,
            "start_date": contact_date,
            "end_date": contact_date,
            "reason": reason,
            "participant_name": participant,
            "effective_date": contact_date,
            "raw_json": None,
            "source": SOURCE_TAG,
        })

    return rows


def map_immunizations(raw_conn, patient_id, provider):
    """Map IMMUNE → immunizations. Includes lot, manufacturer, admin location."""
    rows = []

    for r in raw_conn.execute(
        "SELECT IMMUNE_ID, IMMUNZATN_ID_NAME, IMMUNE_DATE, DOSE, "
        "ROUTE_C_NAME, SITE_C_NAME, MFG_C_NAME, LOT, "
        "GIVEN_BY_USER_ID_NAME, PHYSICAL_SITE FROM IMMUNE"
    ).fetchall():
        immune_id = r[0]
        vaccine_name = r[1]  # IMMUNZATN_ID_NAME
        immune_date = parse_epic_date(r[2])
        dose = r[3]
        route = r[4]
        site = r[5]  # SITE_C_NAME (body site)
        manufacturer = r[6]  # MFG_C_NAME
        lot_number = r[7]  # LOT
        given_by = r[8]  # GIVEN_BY_USER_ID_NAME
        physical_site = r[9]  # PHYSICAL_SITE (admin location)

        if not vaccine_name or not immune_date:
            continue

        fhir_id = f"ehi:imm:{immune_id}"

        rows.append({
            "fhir_id": fhir_id,
            "patient_id": patient_id,
            "provider": provider,
            "vaccine_name": vaccine_name,
            "status": "completed",
            "occurrence_date": immune_date,
            "site": site or "",
            "performer_name": given_by or "",
            "lot_number": lot_number,
            "manufacturer": manufacturer,
            "administering_location": physical_site,
            "effective_date": immune_date,
            "raw_json": None,
            "source": SOURCE_TAG,
        })

    return rows


def map_notes(raw_conn, patient_id, provider):
    """Map HNO_PLAIN_TEXT + Rich Text files → notes.

    HNO_PLAIN_TEXT provides plain text (multiple lines per note).
    Rich Text/ files provide RTF originals (stored as content_rtf).
    NOTE_ENC_INFO provides dates and authors.
    """
    rows = []

    # Aggregate HNO_PLAIN_TEXT by NOTE_ID (lines are stored per-row)
    note_texts = {}  # NOTE_ID → list of (LINE, text)
    for r in raw_conn.execute(
        "SELECT NOTE_CSN_ID, LINE, NOTE_ID, NOTE_TEXT FROM HNO_PLAIN_TEXT"
    ).fetchall():
        line_num = r[1]
        note_id = r[2]
        text = r[3] or ""

        if note_id not in note_texts:
            note_texts[note_id] = []
        note_texts[note_id].append((int(line_num) if line_num else 0, text))

    # Build NOTE_ID → (CONTACT_DATE, AUTHOR, SPEC_NOTE_TIME) from NOTE_ENC_INFO
    note_meta = {}  # NOTE_ID → (date, author)
    try:
        for r in raw_conn.execute(
            "SELECT NOTE_ID, CONTACT_DATE, AUTHOR_USER_ID_NAME, "
            "SPEC_NOTE_TIME_DTTM FROM NOTE_ENC_INFO"
        ).fetchall():
            note_id = r[0]
            contact_date = parse_epic_date(r[1])
            author = r[2] or ""
            spec_time = parse_epic_date(r[3])
            # Prefer specific note time over contact date
            note_meta[note_id] = (spec_time or contact_date, author)
    except Exception:
        pass

    # Build RTF content lookup from _files table: NOTE_ID → RTF bytes
    rtf_content = {}
    try:
        for r in raw_conn.execute(
            "SELECT filename, content FROM _files WHERE directory='Rich Text'"
        ).fetchall():
            filename = r[0]  # e.g., "HNO_12326620_54576_41.RTF"
            # Extract NOTE_ID from filename: HNO_{NOTE_ID}_{...}.RTF
            match = re.match(r'HNO_(\d+)_', filename)
            if match:
                rtf_content[match.group(1)] = r[1]
    except Exception:
        pass

    for note_id, lines in note_texts.items():
        # Sort lines by line number (descending in EHI, we want ascending)
        lines.sort(key=lambda x: x[0])
        content_text = "\n".join(text for _, text in lines).strip()

        if not content_text:
            continue

        # RTF content
        content_rtf = None
        if note_id in rtf_content:
            content_rtf = rtf_content[note_id]
            # If plain text is empty/short but RTF exists, extract text from RTF
            if len(content_text) < 10:
                rtf_text = rtf_to_text(content_rtf)
                if rtf_text:
                    content_text = rtf_text

        # Get date and author from NOTE_ENC_INFO
        meta = note_meta.get(note_id, (None, ""))
        date = meta[0]
        author = meta[1]

        fhir_id = f"ehi:note:{note_id}"

        rows.append({
            "fhir_id": fhir_id,
            "patient_id": patient_id,
            "provider": provider,
            "date": date,
            "doc_type": "clinical note",
            "author": author,
            "content_text": content_text,
            "content_html": None,
            "content_rtf": content_rtf,
            "effective_date": date,
            "raw_json": None,
            "source": SOURCE_TAG,
        })

    return rows


def map_messages(raw_conn, patient_id, provider):
    """Map IB_MESSAGES + IB_NOTES → messages."""
    rows = []

    # Build MSG_ID → body text from IB_NOTES (multi-line)
    msg_bodies = {}
    try:
        for r in raw_conn.execute(
            "SELECT MSG_ID, NOTES, LINE FROM IB_NOTES ORDER BY MSG_ID, LINE"
        ).fetchall():
            msg_id = r[0]
            note_text = r[1] or ""
            if msg_id not in msg_bodies:
                msg_bodies[msg_id] = []
            msg_bodies[msg_id].append(note_text)
    except Exception:
        pass

    for r in raw_conn.execute(
        "SELECT MSG_ID, CREATE_TIME, REGARDING_TOPIC, SENDER_USER_ID_NAME, "
        "PAT_ENC_CSN_ID FROM IB_MESSAGES"
    ).fetchall():
        msg_id = r[0]
        create_time = parse_epic_date(r[1])
        subject = r[2] or ""
        sender = r[3] or ""
        enc_csn = r[4]

        # Build body from IB_NOTES
        body_lines = msg_bodies.get(msg_id, [])
        body = "\n".join(body_lines).strip() if body_lines else ""

        fhir_id = f"ehi:msg:{msg_id}"

        rows.append({
            "fhir_id": fhir_id,
            "patient_id": patient_id,
            "provider": provider,
            "source": SOURCE_TAG,
            "sent_date": create_time,
            "received_date": None,
            "subject": subject,
            "sender": sender,
            "recipient": None,
            "body": body,
            "status": "completed",
            "category": "notification",
            "medium": "mychart",
            "encounter_id": enc_csn,
            "in_response_to": None,
            "raw_json": None,
            "effective_date": create_time,
        })

    return rows


def map_family_history(raw_conn, patient_id, provider):
    """Map FAMILY_HX → family_history."""
    rows = []

    for r in raw_conn.execute("SELECT * FROM FAMILY_HX").fetchall():
        line = r[0]
        condition = r[1]  # MEDICAL_HX_C_NAME
        medical_other = r[2]  # MEDICAL_OTHER
        comments = r[3]  # COMMENTS
        pat_enc_csn = r[4]  # PAT_ENC_CSN_ID
        relation = r[6]  # RELATION_C_NAME
        relation_name = r[7]  # FAM_RELATION_NAME
        age_of_onset = r[8]  # AGE_OF_ONSET
        dx_id = r[10]  # FAM_MEDICAL_DX_ID

        condition_display = condition or medical_other
        if not condition_display:
            continue

        fhir_id = f"ehi:fhx:{line}:{pat_enc_csn}"

        rows.append({
            "fhir_id": fhir_id,
            "patient_id": patient_id,
            "provider": provider,
            "source": SOURCE_TAG,
            "status": "completed",
            "relation": relation or "",
            "relation_name": relation_name or "",
            "relation_sex": None,
            "condition": condition_display,
            "condition_code": dx_id,
            "onset_age": age_of_onset,
            "outcome": None,
            "contributed_to_death": None,
            "date": None,
            "note": comments,
            "raw_json": None,
            "effective_date": None,
        })

    return rows


def map_social_history(raw_conn, patient_id, provider):
    """Map SOCIAL_HX → social_history.

    Converts checkbox-style fields into code_display/value pairs.
    """
    rows = []

    # Social history field mappings: column_index → display name
    SHX_FIELDS = {
        1: ("Cigarettes", "CIGARETTES_YN"),
        2: ("Pipes", "PIPES_YN"),
        3: ("Cigars", "CIGARS_YN"),
        4: ("Snuff", "SNUFF_YN"),
        5: ("Chew", "CHEW_YN"),
        6: ("Alcohol oz/week", "ALCOHOL_OZ_PER_WK"),
        8: ("IV Drug Use", "IV_DRUG_USER_YN"),
    }

    for r in raw_conn.execute("SELECT * FROM SOCIAL_HX").fetchall():
        contact_date = parse_epic_date(r[0])
        pat_enc_csn = r[26] if len(r) > 26 else None  # PAT_ENC_CSN_ID

        if not contact_date:
            continue

        for col_idx, (display_name, field_name) in SHX_FIELDS.items():
            if col_idx >= len(r):
                continue
            value = r[col_idx]
            if not value or value.strip() == "":
                continue

            fhir_id = f"ehi:shx:{field_name}:{pat_enc_csn or contact_date}"

            rows.append({
                "fhir_id": fhir_id,
                "patient_id": patient_id,
                "provider": provider,
                "code_display": display_name,
                "value": value,
                "status": "final",
                "effective_date": contact_date,
                "raw_json": None,
            })

    return rows


# --- Received C-CDA passthrough ---

def process_received_ccdas(raw_conn, unified_db_path, patient_id, source_dir):
    """Process Received C-CDA files through ccda_import.

    Reads C-CDA XML from _files table (or from disk) and delegates to
    ccda_import for parsing. Provider is extracted from the C-CDA custodian.
    """
    # Check for Received C-CDA in _files table
    try:
        ccdas = raw_conn.execute(
            "SELECT filename, content FROM _files WHERE directory='Received C-CDA'"
        ).fetchall()
    except Exception:
        ccdas = []

    if not ccdas:
        return 0

    import tempfile
    import xml.etree.ElementTree as ET
    from .ccda_import import parse_file, insert_rows as ccda_insert_rows

    NS = "urn:hl7-org:v3"
    conn = sqlite3.connect(str(unified_db_path))
    total_inserted = 0

    for filename, content in ccdas:
        if not filename.endswith(".XML") and not filename.endswith(".xml"):
            continue

        try:
            # Write to temp file for parsing
            with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
                tmp.write(content)
                tmp_path = Path(tmp.name)

            # Try to extract provider from custodian organization
            try:
                tree = ET.parse(tmp_path)
                root = tree.getroot()
                custodian_name = None
                for org in root.iter(f"{{{NS}}}representedCustodianOrganization"):
                    name_el = org.find(f"{{{NS}}}name")
                    if name_el is not None and name_el.text:
                        custodian_name = name_el.text.strip()
                        break
                ccda_provider = custodian_name or "External Provider"
            except Exception:
                ccda_provider = "External Provider"

            # Parse and insert
            data = parse_file(str(tmp_path), patient_id, ccda_provider)
            file_total = 0
            for table, rows in data.items():
                n = ccda_insert_rows(conn, table, rows)
                file_total += n
                total_inserted += n

            if file_total > 0:
                print(f"    C-CDA {filename}: {file_total} rows ({ccda_provider})")

            tmp_path.unlink()
        except Exception as e:
            print(f"    C-CDA {filename}: ERROR — {e}", file=sys.stderr)

    conn.close()
    return total_inserted


# --- Main entry point ---

def build_unified_import(source_dir: Path, unified_db_path: Path,
                         raw_db_path: Path = None, provider: str = None,
                         patient_id: str = None):
    """Dual-output EHI import: raw DB + unified DB.

    Args:
        source_dir: Path to the Extracted/ directory (or EHITables/ directly)
        unified_db_path: Path to ehr_data.db (unified schema)
        raw_db_path: Path to ehi_raw.db (default: ehi_raw.db next to source)
        provider: Provider name (auto-detected if not given)
        patient_id: Patient ID in unified DB (auto-detected if not given)
    """
    if not source_dir.exists():
        print(f"ERROR: Source directory not found: {source_dir}", file=sys.stderr)
        sys.exit(1)

    # Default raw DB path
    if raw_db_path is None:
        raw_db_path = source_dir / "ehi_raw.db"

    start = time.time()

    # --- Step 1: Build raw DB (all tables, lossless) ---
    print("=" * 60)
    print("STEP 1: Building raw database (lossless archive)")
    print("=" * 60)
    print()
    build_raw_database(source_dir, raw_db_path)
    print()

    # --- Step 2: Auto-detect patient and provider ---
    print("=" * 60)
    print("STEP 2: Mapping to unified schema")
    print("=" * 60)
    print()

    if not patient_id:
        patient_id, _ = detect_patient_from_ehi(raw_db_path, unified_db_path)
        if not patient_id:
            print("ERROR: Could not auto-detect patient. Use --patient-id.")
            sys.exit(1)

    if not provider:
        provider = detect_provider_from_ehi(raw_db_path)
        if not provider:
            # Fall back to directory name
            provider = source_dir.parent.name if source_dir.name == "Extracted" else source_dir.name
            print(f"  Provider (from directory): {provider}")

    print(f"  Patient ID: {patient_id}")
    print(f"  Provider: {provider}")
    print(f"  Unified DB: {unified_db_path}")
    print()

    # --- Step 3: Map tables to unified schema ---
    raw_conn = sqlite3.connect(str(raw_db_path))
    unified_conn = sqlite3.connect(str(unified_db_path))

    # Check which mappable tables exist in the raw DB
    raw_tables = {row[0] for row in
                  raw_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

    totals = {}

    # Labs
    if "ORDER_RESULTS" in raw_tables:
        lab_rows = map_labs(raw_conn, patient_id, provider)
        n = insert_rows(unified_conn, "labs", lab_rows)
        totals["labs"] = n
        print(f"  labs: {n} new rows (from {len(lab_rows)} mapped)")

    # Allergies
    if "ALLERGY" in raw_tables:
        allergy_rows = map_allergies(raw_conn, patient_id, provider)
        n = insert_rows(unified_conn, "allergies", allergy_rows)
        totals["allergies"] = n
        print(f"  allergies: {n} new rows (from {len(allergy_rows)} mapped)")

    # Medications
    if "ORDER_MED" in raw_tables:
        med_rows = map_medications(raw_conn, patient_id, provider)
        n = insert_rows(unified_conn, "medications", med_rows)
        totals["medications"] = n
        print(f"  medications: {n} new rows (from {len(med_rows)} mapped)")

    # Conditions
    if "PROBLEM_LIST" in raw_tables:
        cond_rows = map_conditions(raw_conn, patient_id, provider)
        n = insert_rows(unified_conn, "conditions", cond_rows)
        totals["conditions"] = n
        print(f"  conditions: {n} new rows (from {len(cond_rows)} mapped)")

    # Vitals
    if "V_EHI_FLO_MEAS_VALUE" in raw_tables:
        vital_rows = map_vitals(raw_conn, patient_id, provider)
        n = insert_rows(unified_conn, "vitals", vital_rows)
        totals["vitals"] = n
        print(f"  vitals: {n} new rows (from {len(vital_rows)} mapped)")

    # Encounters
    if "PAT_ENC" in raw_tables:
        enc_rows = map_encounters(raw_conn, patient_id, provider)
        n = insert_rows(unified_conn, "encounters", enc_rows)
        totals["encounters"] = n
        print(f"  encounters: {n} new rows (from {len(enc_rows)} mapped)")

    # Immunizations
    if "IMMUNE" in raw_tables:
        imm_rows = map_immunizations(raw_conn, patient_id, provider)
        n = insert_rows(unified_conn, "immunizations", imm_rows)
        totals["immunizations"] = n
        print(f"  immunizations: {n} new rows (from {len(imm_rows)} mapped)")

    # Notes
    if "HNO_PLAIN_TEXT" in raw_tables:
        note_rows = map_notes(raw_conn, patient_id, provider)
        n = insert_rows(unified_conn, "notes", note_rows)
        totals["notes"] = n
        print(f"  notes: {n} new rows (from {len(note_rows)} mapped)")

    # Messages
    if "IB_MESSAGES" in raw_tables:
        msg_rows = map_messages(raw_conn, patient_id, provider)
        n = insert_rows(unified_conn, "messages", msg_rows)
        totals["messages"] = n
        print(f"  messages: {n} new rows (from {len(msg_rows)} mapped)")

    # Family History
    if "FAMILY_HX" in raw_tables:
        fhx_rows = map_family_history(raw_conn, patient_id, provider)
        n = insert_rows(unified_conn, "family_history", fhx_rows)
        totals["family_history"] = n
        print(f"  family_history: {n} new rows (from {len(fhx_rows)} mapped)")

    # Social History
    if "SOCIAL_HX" in raw_tables:
        shx_rows = map_social_history(raw_conn, patient_id, provider)
        n = insert_rows(unified_conn, "social_history", shx_rows)
        totals["social_history"] = n
        print(f"  social_history: {n} new rows (from {len(shx_rows)} mapped)")

    unified_conn.close()

    # --- Step 4: Process Received C-CDAs ---
    print()
    ccda_count = process_received_ccdas(raw_conn, unified_db_path, patient_id, source_dir)
    if ccda_count:
        totals["received_ccdas"] = ccda_count
        print(f"  received C-CDAs: {ccda_count} total rows inserted")

    raw_conn.close()

    # --- Summary ---
    elapsed = time.time() - start
    total_rows = sum(totals.values())
    print()
    print("=" * 60)
    print(f"EHI Unified Import Complete — {elapsed:.1f}s")
    print("=" * 60)
    print(f"  Raw DB:     {raw_db_path} ({raw_db_path.stat().st_size / 1024 / 1024:.1f} MB)")
    print(f"  Unified DB: {unified_db_path}")
    print(f"  New rows:   {total_rows}")
    for table, count in totals.items():
        if count > 0:
            print(f"    {table}: {count}")
    print()
