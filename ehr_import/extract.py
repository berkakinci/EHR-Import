"""
Field extraction engine — resolves config path specs against FHIR resources.

All functions are stateless and pure. The path syntax is documented in resources.py.
"""


def resolve_path(resource: dict, path: str):
    """Resolve a dotted path with optional array indexing against a resource dict.

    Supports: "field.subfield", "field[0].subfield", "field.coding[0].code"
    Returns None if any part of the path is missing.
    """
    parts = path.strip().split(".")
    current = resource
    for part in parts:
        if current is None:
            return None
        # Handle array index: "field[0]"
        if "[" in part:
            field, idx_str = part.split("[", 1)
            idx = int(idx_str.rstrip("]"))
            current = current.get(field) if isinstance(current, dict) else None
            if isinstance(current, list) and len(current) > idx:
                current = current[idx]
            else:
                return None
        else:
            current = current.get(part) if isinstance(current, dict) else None
    return current


def extract_field(resource: dict, spec: str) -> str | int | None:
    """Extract a field value from a FHIR resource using the config spec syntax.

    Supports fallback chains ("|"), special extractors ("@prefix:"), and plain paths.
    """
    # Handle fallback chains
    if "|" in spec and not spec.startswith("@"):
        for path in spec.split("|"):
            val = resolve_path(resource, path.strip())
            if val is not None:
                return str(val) if not isinstance(val, (int, float, bool)) else val
        return None

    # Special extractors
    if spec.startswith("@coding_display:"):
        field_path = spec[len("@coding_display:"):]
        obj = resolve_path(resource, field_path) if field_path else resource
        return _get_coding_display(obj) if obj else None

    if spec == "@value:":
        return _extract_value(resource)

    if spec == "@unit:":
        return _extract_unit(resource)

    if spec == "@ref_range:":
        return _extract_reference_range(resource)

    if spec == "@author:":
        return _extract_author(resource)

    if spec == "@reactions:":
        return _extract_reactions(resource)

    if spec == "@dosage:":
        return _extract_dosage(resource)

    if spec == "@med_name:":
        return _extract_med_name(resource)

    if spec.startswith("@join:"):
        field_path = spec[len("@join:"):]
        val = resolve_path(resource, field_path)
        if isinstance(val, list):
            return ", ".join(str(v) for v in val if v)
        return str(val) if val else None

    # Plain path
    val = resolve_path(resource, spec)
    if isinstance(val, bool):
        return 1 if val else 0
    if val is not None:
        return str(val)
    return None


def extract_effective_date(resource: dict, date_paths: list[str] | None) -> str | None:
    """Extract the best effective date from a resource using priority list."""
    if not date_paths:
        return None
    for path in date_paths:
        val = resolve_path(resource, path)
        if val:
            return str(val)
    return None


# =============================================================================
# Special extractors (complex logic that can't be expressed as paths)
# =============================================================================

def _get_coding_display(codeable_concept) -> str | None:
    """Get display text from a CodeableConcept or similar."""
    if not codeable_concept or not isinstance(codeable_concept, dict):
        return None
    text = codeable_concept.get("text")
    if text:
        return text
    for coding in codeable_concept.get("coding", []):
        if coding.get("display"):
            return coding["display"]
    return None


def _extract_value(observation: dict) -> str | None:
    """Extract the value from an Observation resource."""
    if "valueQuantity" in observation:
        return str(observation["valueQuantity"].get("value", ""))
    if "valueString" in observation:
        return observation["valueString"]
    if "valueCodeableConcept" in observation:
        return _get_coding_display(observation["valueCodeableConcept"])
    if "component" in observation:
        parts = []
        for comp in observation["component"]:
            name = _get_coding_display(comp.get("code", {}))
            val = _extract_value(comp)
            parts.append(f"{name}: {val}")
        return "; ".join(parts)
    return None


def _extract_unit(observation: dict) -> str | None:
    """Extract unit from an Observation."""
    if "valueQuantity" in observation:
        return observation["valueQuantity"].get("unit") or observation["valueQuantity"].get("code")
    return None


def _extract_reference_range(observation: dict) -> str | None:
    """Extract reference range text."""
    ranges = observation.get("referenceRange", [])
    if not ranges:
        return None
    r = ranges[0]
    low = r.get("low", {}).get("value")
    high = r.get("high", {}).get("value")
    if low is not None and high is not None:
        return f"{low}-{high}"
    return r.get("text")


def _extract_author(doc_ref: dict) -> str | None:
    """Extract author name from DocumentReference."""
    authors = doc_ref.get("author", [])
    return authors[0].get("display") if authors else None


def _extract_reactions(allergy: dict) -> str | None:
    """Extract reactions summary from AllergyIntolerance."""
    reactions = allergy.get("reaction", [])
    if not reactions:
        return None
    parts = []
    for r in reactions:
        for m in r.get("manifestation", []):
            text = m.get("text") or _get_coding_display(m)
            if text:
                parts.append(text)
    return "; ".join(parts) if parts else None


def _extract_dosage(med: dict) -> str | None:
    """Extract dosage text from MedicationRequest."""
    dosage_list = med.get("dosageInstruction", [])
    if not dosage_list:
        return None
    texts = [d.get("text", "") for d in dosage_list if d.get("text")]
    return "; ".join(texts) if texts else None


def _extract_med_name(med: dict) -> str | None:
    """Extract medication name from MedicationRequest or MedicationDispense."""
    concept = med.get("medicationCodeableConcept", {})
    name = concept.get("text") or _get_coding_display(concept)
    if not name and med.get("medicationReference"):
        name = med["medicationReference"].get("display")
    return name
