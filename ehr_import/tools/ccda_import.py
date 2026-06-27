"""
Import C-CDA XML exports into the unified ehr_data.db schema.

Parses standard C-CDA sections and writes into the same tables used by FHIR
pulls, with a `source` column to distinguish origin. Uses content-based
synthetic fhir_ids for idempotent deduplication.

Tested with eClinicalWorks/healow Personal Health Record exports.

Usage:
    python ccda_import.py --source /path/to/xml/dir --db ./ehr_data.db --patient-id <id>
"""

import hashlib
import json
import sqlite3
import xml.etree.ElementTree as ET
import re
from pathlib import Path
from datetime import datetime

# HL7 CDA namespace
NS = "urn:hl7-org:v3"

SOURCE_TAG = "ccda_ecw"


def tag(name):
    """Return fully qualified tag name."""
    return f"{{{NS}}}{name}"


def make_id(*parts):
    """Generate a deterministic synthetic fhir_id from content parts."""
    content = "|".join(str(p) for p in parts if p)
    h = hashlib.sha256(content.encode()).hexdigest()[:16]
    return f"ccda:{h}"


def parse_date(date_str):
    """Parse HL7 date string (YYYYMMDD or YYYYMMDDHHmmSS±HHMM) to ISO date."""
    if not date_str:
        return None
    date_str = re.sub(r'[+-]\d{4}$', '', date_str)
    try:
        if len(date_str) >= 14:
            dt = datetime.strptime(date_str[:14], "%Y%m%d%H%M%S")
            return dt.strftime("%Y-%m-%dT%H:%M:%S")
        elif len(date_str) >= 8:
            dt = datetime.strptime(date_str[:8], "%Y%m%d")
            return dt.strftime("%Y-%m-%d")
    except ValueError:
        pass
    return date_str


def get_person_name(name_el):
    """Extract person name from a <name> element."""
    if name_el is None:
        return ""
    given = name_el.find(tag("given"))
    family = name_el.find(tag("family"))
    suffix = name_el.find(tag("suffix"))
    parts = []
    if given is not None and given.text:
        parts.append(given.text.strip())
    if family is not None and family.text:
        parts.append(family.text.strip())
    if suffix is not None and suffix.text:
        parts.append(suffix.text.strip())
    return " ".join(parts)


def extract_text_content(el):
    """Extract all text content from an element, stripping XML tags."""
    if el is None:
        return ""
    raw = ET.tostring(el, encoding="unicode", method="text")
    return re.sub(r'\s+', ' ', raw).strip()


def render_narrative_item(item_el):
    """Render a C-CDA narrative <item> to readable text and cleaned HTML.

    Handles tables with rowspan, paragraphs with line breaks.
    Returns (text, html) tuple.
    """
    if item_el is None:
        return "", ""

    # HTML: strip namespace prefixes for readability
    raw_html = ET.tostring(item_el, encoding="unicode")
    html = re.sub(r'</?ns0:', '<', raw_html)
    html = re.sub(r' xmlns:ns0="[^"]*"', '', html)
    html = re.sub(r'</?ns\d+:', '<', html)
    html = re.sub(r' xmlns:[^=]+="[^"]*"', '', html)

    # Text: smart extraction with rowspan tracking and paragraph breaks
    lines = []

    # Handle top-level paragraphs (section titles like "HPI", "Examination")
    for child in item_el:
        if child.tag == tag("paragraph"):
            text = (child.text or "").strip()
            if text:
                lines.append(text)
                lines.append("")

    # Handle tables
    for table in item_el.iter(tag("table")):
        # Collect headers
        headers = []
        thead = table.find(tag("thead"))
        if thead is not None:
            for th in thead.iter(tag("th")):
                headers.append((th.text or "").strip())

        # Process body rows with rowspan tracking
        tbody = table.find(tag("tbody"))
        if tbody is None:
            continue

        # rowspan_carry[col_idx] = (remaining_count, value)
        rowspan_carry = {}

        for tr in tbody.findall(tag("tr")):
            tds = tr.findall(tag("td"))
            row_values = []
            td_idx = 0

            for col_idx in range(len(headers) or 20):
                # Check if we have a carried-over rowspan value
                if col_idx in rowspan_carry:
                    remaining, val = rowspan_carry[col_idx]
                    row_values.append(val)
                    if remaining <= 1:
                        del rowspan_carry[col_idx]
                    else:
                        rowspan_carry[col_idx] = (remaining - 1, val)
                elif td_idx < len(tds):
                    td = tds[td_idx]
                    td_idx += 1

                    # Check for rowspan
                    rowspan = int(td.get("rowspan", "1"))
                    colspan = int(td.get("colspan", "1"))

                    # Extract cell content (handle paragraphs)
                    cell_parts = []
                    if td.text and td.text.strip():
                        cell_parts.append(td.text.strip())
                    for sub in td:
                        if sub.tag == tag("paragraph"):
                            p_text = extract_text_content(sub)
                            if p_text:
                                cell_parts.append(p_text)
                        elif sub.text and sub.text.strip():
                            cell_parts.append(sub.text.strip())
                    cell_text = "\n\n".join(cell_parts) if cell_parts else ""

                    row_values.append(cell_text)
                    if rowspan > 1:
                        rowspan_carry[col_idx] = (rowspan - 1, cell_text)
                    # Skip columns covered by colspan
                    for _ in range(colspan - 1):
                        col_idx += 1
                else:
                    row_values.append("")

                if col_idx >= (len(headers) or 20) - 1:
                    break

            # Format the row
            # Filter out empty values and join with separator
            non_empty = [(headers[i] if i < len(headers) else "", v)
                         for i, v in enumerate(row_values) if v.strip()]
            if non_empty:
                # Check if any cell has multi-paragraph content
                has_long = any("\n" in v for _, v in non_empty)
                if has_long:
                    # Header cells on first line, long content below
                    short_parts = []
                    long_parts = []
                    for h, v in non_empty:
                        if "\n" in v:
                            long_parts.append(v)
                        else:
                            label = f"{h}: {v}" if h else v
                            short_parts.append(label)
                    if short_parts:
                        lines.append(" | ".join(short_parts))
                    for lp in long_parts:
                        lines.append(lp)
                else:
                    parts = []
                    for h, v in non_empty:
                        label = f"{h}: {v}" if h else v
                        parts.append(label)
                    lines.append(" | ".join(parts))

        lines.append("")  # blank line after table

    text = "\n".join(lines).strip()
    return text, html


# --- Section parsers ---


def parse_results(section, patient_id, provider_name):
    """Parse Results (lab) section into labs table rows."""
    rows = []
    for entry in section.findall(tag("entry")):
        org = entry.find(tag("organizer"))
        if org is None:
            continue

        for comp in org.findall(tag("component")):
            obs = comp.find(tag("observation"))
            if obs is None:
                continue

            obs_code = obs.find(tag("code"))
            obs_name = obs_code.get("displayName", "") if obs_code is not None else ""
            loinc = obs_code.get("code", "") if obs_code is not None else ""

            et = obs.find(tag("effectiveTime"))
            date = parse_date(et.get("value", "")) if et is not None else None

            value_el = obs.find(tag("value"))
            val = ""
            unit = ""
            if value_el is not None:
                val = value_el.get("value", "")
                if not val and value_el.text:
                    val = value_el.text.strip()
                unit = value_el.get("unit", "")

            ref_range = obs.find(f"{tag('referenceRange')}//{tag('text')}")
            ref_text = ref_range.text.strip() if ref_range is not None and ref_range.text else ""

            if not date or not val:
                continue

            code_display = obs_name or loinc
            fhir_id = make_id(patient_id, date, code_display, val)

            rows.append({
                "fhir_id": fhir_id,
                "patient_id": patient_id,
                "provider": provider_name,
                "code_display": code_display,
                "value": val,
                "unit": unit,
                "reference_range": ref_text,
                "status": "final",
                "effective_date": date,
                "raw_json": None,
                "source": SOURCE_TAG,
            })

    return rows


def parse_encounters(section, patient_id, provider_name):
    """Parse Encounters section into encounters table rows."""
    rows = []
    for entry in section.findall(tag("entry")):
        enc = entry.find(tag("encounter"))
        if enc is None:
            continue

        et = enc.find(tag("effectiveTime"))
        date = None
        if et is not None:
            low = et.find(tag("low"))
            date = parse_date(low.get("value", "")) if low is not None else None

        if not date:
            continue

        code_el = enc.find(tag("code"))
        enc_type = ""
        if code_el is not None:
            trans = code_el.find(tag("translation"))
            if trans is not None:
                enc_type = trans.get("displayName", "")

        performer = enc.find(f".//{tag('performer')}//{tag('assignedPerson')}/{tag('name')}")
        performer_name = get_person_name(performer)

        performer_id = enc.find(
            f".//{tag('performer')}//{tag('id')}[@assigningAuthorityName='National Provider ID']")
        npi = performer_id.get("extension", "") if performer_id is not None else ""

        # First diagnosis as reason
        reason = ""
        er = enc.find(f"{tag('entryRelationship')}")
        if er is not None:
            obs = er.find(f".//{tag('observation')}")
            if obs is not None:
                val = obs.find(tag("value"))
                if val is not None:
                    trans = val.find(tag("translation"))
                    if trans is not None:
                        reason = trans.get("displayName", "")

        fhir_id = make_id(patient_id, date, npi or performer_name)

        rows.append({
            "fhir_id": fhir_id,
            "patient_id": patient_id,
            "provider": provider_name,
            "encounter_type": enc_type,
            "status": "finished",
            "class": "ambulatory",
            "start_date": date,
            "end_date": date,
            "reason": reason,
            "participant_name": performer_name,
            "effective_date": date,
            "raw_json": None,
            "source": SOURCE_TAG,
        })

    return rows


def parse_vitals(section, patient_id, provider_name):
    """Parse Vital Signs section into vitals table rows."""
    rows = []
    for entry in section.findall(tag("entry")):
        org = entry.find(tag("organizer"))
        if org is None:
            continue
        for comp in org.findall(tag("component")):
            obs = comp.find(tag("observation"))
            if obs is None:
                continue
            obs_code = obs.find(tag("code"))
            obs_name = obs_code.get("displayName", "") if obs_code is not None else ""
            et = obs.find(tag("effectiveTime"))
            date = parse_date(et.get("value", "")) if et is not None else None
            value_el = obs.find(tag("value"))
            val = ""
            unit = ""
            if value_el is not None:
                val = value_el.get("value", "")
                unit = value_el.get("unit", "")
            if not date or not val:
                continue
            fhir_id = make_id(patient_id, date, obs_name, val)
            rows.append({
                "fhir_id": fhir_id,
                "patient_id": patient_id,
                "provider": provider_name,
                "code_display": obs_name,
                "value": val,
                "unit": unit,
                "status": "final",
                "effective_date": date,
                "raw_json": None,
                "source": SOURCE_TAG,
            })
    return rows


def parse_conditions(section, patient_id, provider_name):
    """Parse Problems section into conditions table rows."""
    rows = []
    for entry in section.findall(tag("entry")):
        act = entry.find(tag("act"))
        if act is None:
            continue
        for er in act.findall(tag("entryRelationship")):
            obs = er.find(tag("observation"))
            if obs is None:
                continue
            val = obs.find(tag("value"))
            code = ""
            display = ""
            if val is not None:
                code = val.get("code", "")
                display = val.get("displayName", "")
                trans = val.find(tag("translation"))
                if trans is not None:
                    code = trans.get("code", code)
                    display = trans.get("displayName", display)
            et = obs.find(tag("effectiveTime"))
            onset = None
            abatement = None
            if et is not None:
                low = et.find(tag("low"))
                high = et.find(tag("high"))
                if low is not None:
                    onset = parse_date(low.get("value", ""))
                if high is not None:
                    abatement = parse_date(high.get("value", ""))
            status_el = obs.find(f"{tag('entryRelationship')}//{tag('value')}")
            clinical_status = ""
            if status_el is not None:
                clinical_status = status_el.get("displayName", "")
            if not display and not code:
                continue
            fhir_id = make_id(patient_id, code or display, onset)
            rows.append({
                "fhir_id": fhir_id,
                "patient_id": patient_id,
                "provider": provider_name,
                "code_display": display,
                "clinical_status": clinical_status or "active",
                "verification_status": "confirmed",
                "category": "Problem List Item",
                "onset_date": onset,
                "abatement_date": abatement,
                "effective_date": onset,
                "raw_json": None,
                "source": SOURCE_TAG,
            })
    return rows


def parse_immunizations(section, patient_id, provider_name):
    """Parse Immunizations section into immunizations table rows."""
    rows = []
    for entry in section.findall(tag("entry")):
        sa = entry.find(tag("substanceAdministration"))
        if sa is None:
            continue
        et = sa.find(tag("effectiveTime"))
        date = parse_date(et.get("value", "")) if et is not None else None
        med_el = sa.find(f".//{tag('manufacturedMaterial')}/{tag('code')}")
        vaccine_name = med_el.get("displayName", "") if med_el is not None else ""
        cvx = med_el.get("code", "") if med_el is not None else ""
        if not date:
            continue
        performer_el = sa.find(f".//{tag('performer')}//{tag('assignedPerson')}/{tag('name')}")
        performer = get_person_name(performer_el)
        fhir_id = make_id(patient_id, date, cvx or vaccine_name)
        rows.append({
            "fhir_id": fhir_id,
            "patient_id": patient_id,
            "provider": provider_name,
            "vaccine_name": vaccine_name,
            "status": "completed",
            "occurrence_date": date,
            "site": "",
            "performer_name": performer,
            "effective_date": date,
            "raw_json": None,
            "source": SOURCE_TAG,
        })
    return rows


def parse_medications(section, patient_id, provider_name):
    """Parse Medications section into medications table rows."""
    rows = []
    for entry in section.findall(tag("entry")):
        sa = entry.find(f".//{tag('substanceAdministration')}")
        if sa is None:
            continue
        status_el = sa.find(tag("statusCode"))
        status = status_el.get("code", "") if status_el is not None else ""
        start_date = None
        for et in sa.findall(tag("effectiveTime")):
            low = et.find(tag("low"))
            if low is not None and low.get("value"):
                start_date = parse_date(low.get("value"))
                break
        med_el = sa.find(f".//{tag('manufacturedMaterial')}/{tag('code')}")
        med_name = med_el.get("displayName", "") if med_el is not None else ""
        if not med_name:
            continue
        dose_el = sa.find(tag("doseQuantity"))
        dosage = ""
        if dose_el is not None:
            dv = dose_el.get("value", "")
            du = dose_el.get("unit", "")
            dosage = f"{dv} {du}".strip()
        fhir_id = make_id(patient_id, med_name, start_date)
        rows.append({
            "fhir_id": fhir_id,
            "patient_id": patient_id,
            "provider": provider_name,
            "medication_name": med_name,
            "status": status,
            "intent": "order",
            "reported": 0,
            "authored_on": start_date,
            "dosage_text": dosage,
            "requester": "",
            "effective_date": start_date,
            "raw_json": None,
            "source": SOURCE_TAG,
        })
    return rows


def parse_treatment_plans(section, patient_id, provider_name):
    """Parse Assessments section (encounter-linked diagnosis + treatment plans)."""
    rows = []
    text_el = section.find(tag("text"))
    if text_el is None:
        return rows
    for tr in text_el.iter(tag("tr")):
        tds = tr.findall(tag("td"))
        if len(tds) < 2:
            continue
        date_text = extract_text_content(tds[0]).strip()
        if not date_text or date_text == "Encounter Date":
            continue
        try:
            dt = datetime.strptime(date_text, "%m/%d/%Y")
            date = dt.strftime("%Y-%m-%d")
        except ValueError:
            date = date_text
        diagnosis = extract_text_content(tds[1]).strip() if len(tds) > 1 else ""
        treatment_notes = extract_text_content(tds[2]).strip() if len(tds) > 2 else ""
        section_notes = extract_text_content(tds[3]).strip() if len(tds) > 3 else ""
        fhir_id = make_id(patient_id, date, diagnosis[:60])
        rows.append({
            "fhir_id": fhir_id,
            "patient_id": patient_id,
            "source": SOURCE_TAG,
            "provider": provider_name,
            "date": date,
            "diagnosis": diagnosis,
            "treatment_notes": treatment_notes,
            "section_notes": section_notes,
        })
    return rows


def parse_notes_with_content(section, patient_id, provider_name, note_type="progress"):
    """Parse notes section, resolving narrative content from text block."""
    rows = []

    # Build a map of text_ref ID → narrative item element
    text_el = section.find(tag("text"))
    narrative_map = {}
    if text_el is not None:
        for item in text_el.iter(tag("item")):
            item_id = item.get("ID", "")
            if item_id:
                narrative_map[f"#{item_id}"] = item

    for entry in section.findall(tag("entry")):
        act = entry.find(tag("act"))
        if act is None:
            continue
        et = act.find(tag("effectiveTime"))
        date = parse_date(et.get("value", "")) if et is not None else None
        if not date:
            continue
        author_el = act.find(f".//{tag('author')}//{tag('assignedPerson')}/{tag('name')}")
        author = get_person_name(author_el)
        text_ref_el = act.find(f"{tag('text')}/{tag('reference')}")
        ref_value = text_ref_el.get("value", "") if text_ref_el is not None else ""

        # Resolve content from narrative block
        content_text = ""
        content_html = ""
        if ref_value and ref_value in narrative_map:
            content_text, content_html = render_narrative_item(narrative_map[ref_value])

        fhir_id = make_id(patient_id, date, note_type, author)
        rows.append({
            "fhir_id": fhir_id,
            "patient_id": patient_id,
            "provider": provider_name,
            "date": date,
            "doc_type": note_type,
            "author": author,
            "content_text": content_text,
            "content_html": content_html,
            "effective_date": date,
            "raw_json": None,
            "source": SOURCE_TAG,
        })

    return rows


# --- Section code to parser mapping ---
SECTION_PARSERS = {
    "30954-2": "results",
    "46240-8": "encounters",
    "8716-3": "vitals",
    "11450-4": "conditions",
    "11369-6": "immunizations",
    "10160-0": "medications",
    "51848-0": "assessments",
    "11506-3": "notes_progress",
    "34117-2": "notes_hpi",
}


def parse_file(filepath, patient_id, provider_name):
    """Parse a single C-CDA XML file and return all data as table-keyed dict."""
    tree = ET.parse(filepath)
    root = tree.getroot()

    all_data = {
        "labs": [], "encounters": [], "vitals": [], "conditions": [],
        "immunizations": [], "medications": [], "treatment_plans": [], "notes": [],
    }

    for section in root.iter(tag("section")):
        code_el = section.find(tag("code"))
        if code_el is None:
            continue
        code = code_el.get("code", "")
        if code not in SECTION_PARSERS:
            continue
        section_key = SECTION_PARSERS[code]

        if section_key == "results":
            all_data["labs"].extend(parse_results(section, patient_id, provider_name))
        elif section_key == "encounters":
            all_data["encounters"].extend(parse_encounters(section, patient_id, provider_name))
        elif section_key == "vitals":
            all_data["vitals"].extend(parse_vitals(section, patient_id, provider_name))
        elif section_key == "conditions":
            all_data["conditions"].extend(parse_conditions(section, patient_id, provider_name))
        elif section_key == "immunizations":
            all_data["immunizations"].extend(
                parse_immunizations(section, patient_id, provider_name))
        elif section_key == "medications":
            all_data["medications"].extend(
                parse_medications(section, patient_id, provider_name))
        elif section_key == "assessments":
            all_data["treatment_plans"].extend(
                parse_treatment_plans(section, patient_id, provider_name))
        elif section_key == "notes_progress":
            all_data["notes"].extend(
                parse_notes_with_content(section, patient_id, provider_name, "progress"))
        elif section_key == "notes_hpi":
            all_data["notes"].extend(
                parse_notes_with_content(section, patient_id, provider_name, "hpi"))

    return all_data


def insert_rows(conn, table, rows):
    """Insert rows with INSERT OR IGNORE for dedup."""
    if not rows:
        return 0
    # Use the keys from the first row as columns
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


def detect_patient_id(sample_xml: Path, db_path: Path):
    """Auto-detect patient_id by matching C-CDA demographics against the DB.

    Extracts given name + family name + DOB from the C-CDA recordTarget, then
    queries the patients table for a match. Returns the patient_id if exactly
    one match, or None with an error message if ambiguous/missing.
    """
    # Extract demographics from C-CDA
    tree = ET.parse(sample_xml)
    root = tree.getroot()

    given_name = None
    family_name = None
    birth_date = None

    for pr in root.iter(tag("patientRole")):
        patient = pr.find(tag("patient"))
        if patient is not None:
            name_el = patient.find(tag("name"))
            if name_el is not None:
                giv = name_el.find(tag("given"))
                if giv is not None and giv.text:
                    given_name = giv.text.strip()
                fam = name_el.find(tag("family"))
                if fam is not None and fam.text:
                    family_name = fam.text.strip()
            bday = patient.find(tag("birthTime"))
            if bday is not None:
                raw = bday.get("value", "")
                if len(raw) >= 8:
                    birth_date = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"

    if not given_name or not family_name or not birth_date:
        print(f"ERROR: Could not extract patient demographics from {sample_xml.name}")
        print(f"  Use --patient-id to specify manually.")
        return None

    print(f"  C-CDA patient: {given_name} {family_name}, DOB {birth_date}")

    # Query DB for matches (given + family + DOB)
    if not db_path.exists():
        print(f"ERROR: Database {db_path} does not exist. Use --patient-id to specify manually.")
        return None

    conn = sqlite3.connect(db_path)
    # Match on first name initial + family name + DOB to handle "Kiara" vs "Kiara F"
    rows = conn.execute(
        "SELECT patient_id, given_name, family_name, provider FROM patients "
        "WHERE LOWER(family_name) = LOWER(?) AND birth_date = ? "
        "AND LOWER(SUBSTR(given_name, 1, ?)) = LOWER(?)",
        (family_name, birth_date, len(given_name), given_name)
    ).fetchall()
    conn.close()

    if len(rows) == 0:
        print(f"ERROR: No patient match for {given_name} {family_name}, "
              f"DOB {birth_date} in {db_path.name}")
        print(f"  Use --patient-id to specify manually.")
        return None
    elif len(rows) == 1:
        pid = rows[0][0]
        print(f"  Matched: {rows[0][1]} {rows[0][2]} ({rows[0][3]}) → {pid}")
        return pid
    else:
        # Multiple matches (same person at different providers)
        print(f"  Multiple patient records match {given_name} {family_name}, DOB {birth_date}:")
        for pid, given, fam, prov in rows:
            print(f"    {given} {fam} ({prov}) → {pid}")
        # Use the first one — build scripts already query by list of patient IDs
        selected = rows[0][0]
        print(f"  Using: {selected}")
        print(f"  (Override with --patient-id if needed)")
        return selected


def build_database(source_dir: Path, db_path: Path, patient_id: str = None,
                   provider_name: str = "Allergy & Asthma Specialists"):
    """Main entry point: parse all XML files and insert into unified DB."""
    xml_files = sorted(source_dir.glob("*.xml"))
    if not xml_files:
        print(f"ERROR: No XML files found in {source_dir}")
        return

    # Auto-detect patient_id if not provided
    if not patient_id:
        patient_id = detect_patient_id(xml_files[0], db_path)
        if not patient_id:
            return  # error already printed

    print(f"Found {len(xml_files)} XML files in:")
    print(f"  {source_dir}")
    print(f"  Patient ID: {patient_id}")
    print(f"  Provider: {provider_name}")
    print(f"  Target DB: {db_path}")
    print()

    conn = sqlite3.connect(db_path)

    totals = {"labs": 0, "encounters": 0, "vitals": 0, "conditions": 0,
              "immunizations": 0, "medications": 0, "treatment_plans": 0, "notes": 0}

    for xml_file in xml_files:
        print(f"  {xml_file.name}...", end=" ")
        try:
            data = parse_file(xml_file, patient_id, provider_name)
            file_total = 0
            for table, rows in data.items():
                n = insert_rows(conn, table, rows)
                totals[table] += n
                file_total += n
            print(f"{file_total} new rows")
        except ET.ParseError as e:
            print(f"PARSE ERROR: {e}")
        except Exception as e:
            print(f"ERROR: {e}")
            import traceback
            traceback.print_exc()

    conn.close()

    print(f"\nImport complete. New rows by table:")
    for table, count in totals.items():
        print(f"  {table}: {count}")
    print(f"  TOTAL: {sum(totals.values())}")
