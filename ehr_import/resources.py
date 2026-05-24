"""
Resource configuration — defines what to pull from FHIR and how to store it.

Each entry in RESOURCES configures:
  - How to search for the resource (FHIR resource type, search params)
  - Which convenience table to materialize it into (if any)
  - Which fields to extract into columns (curated mapping)
  - Hooks for special handling (dedup, content_fetch, effective_date)

The generic `resources` table stores everything regardless of whether a
convenience table exists. Convenience tables are derived views for ergonomic
querying of the resource types you care about most.
"""

# --- Column extraction path syntax ---
# "field"                    -> resource["field"]
# "field.subfield"           -> resource["field"]["subfield"]
# "field[0].subfield"        -> resource["field"][0]["subfield"]
# "field.coding[0].code"     -> resource["field"]["coding"][0]["code"]
# "a | b"                    -> first non-None of a, b (fallback chain)
# Special prefixes:
#   "@coding_display:"       -> _get_coding_display(resource[field])
#   "@value:"                -> _extract_value(resource) (Observation-specific)
#   "@unit:"                 -> _extract_unit(resource) (Observation-specific)
#   "@ref_range:"            -> _extract_reference_range(resource)
#   "@author:"               -> _extract_author(resource) (DocumentReference)
#   "@reactions:"            -> _extract_reactions(resource) (AllergyIntolerance)
#   "@dosage:"               -> _extract_dosage(resource) (MedicationRequest)
#   "@med_name:"             -> _extract_med_name(resource) (MedicationRequest)
#   "@join:"                 -> join list items (e.g., category list)


RESOURCES = [
    # --- Observations (split by category) ---
    {
        "fhir_type": "Observation",
        "label": "Observation (labs)",
        "search_params": {"category": "laboratory"},
        "table": "labs",
        "columns": {
            "code_display": "@coding_display:code",
            "value": "@value:",
            "unit": "@unit:",
            "reference_range": "@ref_range:",
            "status": "status",
        },
        "effective_date": ["effectiveDateTime", "effectivePeriod.start"],
    },
    {
        "fhir_type": "Observation",
        "label": "Observation (vitals)",
        "search_params": {"category": "vital-signs"},
        "table": "vitals",
        "columns": {
            "code_display": "@coding_display:code",
            "value": "@value:",
            "unit": "@unit:",
            "status": "status",
        },
        "effective_date": ["effectiveDateTime", "effectivePeriod.start"],
    },
    {
        "fhir_type": "Observation",
        "label": "Observation (social history)",
        "search_params": {"category": "social-history"},
        "table": "social_history",
        "columns": {
            "code_display": "@coding_display:code",
            "value": "@value:",
            "status": "status",
        },
        "effective_date": ["effectiveDateTime", "effectivePeriod.start"],
    },
    {
        "fhir_type": "Observation",
        "label": "Observation (assessments)",
        "search_params": {"category": "survey"},
        "table": "assessments",
        "columns": {
            "code_display": "@coding_display:code",
            "value": "@value:",
            "status": "status",
        },
        "effective_date": ["effectiveDateTime", "effectivePeriod.start"],
    },

    # --- DiagnosticReport ---
    {
        "fhir_type": "DiagnosticReport",
        "label": "DiagnosticReport",
        "search_params": {},
        "table": "diagnostic_reports",
        "columns": {
            "code_display": "@coding_display:code",
            "status": "status",
        },
        "effective_date": ["effectiveDateTime", "effectivePeriod.start"],
        "content_fetch": "presentedForm",
    },

    # --- DocumentReference (notes) ---
    {
        "fhir_type": "DocumentReference",
        "label": "DocumentReference (notes)",
        "search_params": {"category": "clinical-note"},
        "table": "notes",
        "columns": {
            "doc_type": "@coding_display:type",
            "author": "@author:",
            "date": "date | context.period.start",
            "status": "status",
        },
        "effective_date": ["date", "context.period.start"],
        "content_fetch": "content",
    },

    # --- Condition ---
    {
        "fhir_type": "Condition",
        "label": "Condition",
        "search_params": {},
        "table": "conditions",
        "columns": {
            "code_display": "@coding_display:code",
            "clinical_status": "clinicalStatus.coding[0].code",
            "verification_status": "verificationStatus.coding[0].code",
            "category": "@coding_display:category[0]",
            "onset_date": "onsetDateTime | onsetPeriod.start",
            "abatement_date": "abatementDateTime | abatementPeriod.start",
        },
        "effective_date": ["onsetDateTime", "onsetPeriod.start", "recordedDate"],
    },

    # --- AllergyIntolerance ---
    {
        "fhir_type": "AllergyIntolerance",
        "label": "AllergyIntolerance",
        "search_params": {},
        "table": "allergies",
        "columns": {
            "code_display": "@coding_display:code",
            "clinical_status": "clinicalStatus.coding[0].code",
            "verification_status": "verificationStatus.coding[0].code",
            "type": "type",
            "category": "@join:category",
            "criticality": "criticality",
            "onset_date": "onsetDateTime | onsetPeriod.start",
            "recorded_date": "recordedDate",
            "reaction_text": "@reactions:",
        },
        "effective_date": ["onsetDateTime", "onsetPeriod.start", "recordedDate"],
        "dedup": "case_insensitive:code_display",
    },

    # --- Encounter ---
    {
        "fhir_type": "Encounter",
        "label": "Encounter",
        "search_params": {},
        "table": "encounters",
        "columns": {
            "encounter_type": "@coding_display:type[0]",
            "status": "status",
            "class": "class.display | class.code",
            "start_date": "period.start",
            "end_date": "period.end",
            "reason": "@coding_display:reasonCode[0]",
            "participant_name": "participant[0].individual.display",
        },
        "effective_date": ["period.start"],
    },

    # --- MedicationRequest ---
    {
        "fhir_type": "MedicationRequest",
        "label": "MedicationRequest",
        "search_params": {},
        "table": "medications",
        "columns": {
            "medication_name": "@med_name:",
            "status": "status",
            "intent": "intent",
            "reported": "reportedBoolean",
            "authored_on": "authoredOn",
            "dosage_text": "@dosage:",
            "requester": "requester.display",
        },
        "effective_date": ["authoredOn"],
    },

    # --- Immunization ---
    {
        "fhir_type": "Immunization",
        "label": "Immunization",
        "search_params": {},
        "table": "immunizations",
        "columns": {
            "vaccine_name": "@coding_display:vaccineCode",
            "status": "status",
            "occurrence_date": "occurrenceDateTime | occurrenceString",
            "site": "@coding_display:site",
            "performer_name": "performer[0].actor.display",
        },
        "effective_date": ["occurrenceDateTime"],
    },

    # --- MedicationDispense ---
    {
        "fhir_type": "MedicationDispense",
        "label": "MedicationDispense",
        "search_params": {},
        "table": "medication_dispenses",
        "columns": {
            "medication_name": "@med_name:",
            "status": "status",
            "quantity": "quantity.value",
            "days_supply": "daysSupply.value",
            "when_handed_over": "whenHandedOver",
            "performer_name": "performer[0].actor.display",
        },
        "effective_date": ["whenHandedOver", "whenPrepared"],
    },

    # --- Procedure ---
    {
        "fhir_type": "Procedure",
        "label": "Procedure",
        "search_params": {},
        "table": "procedures",
        "columns": {
            "code_display": "@coding_display:code",
            "status": "status",
            "performed_date": "performedDateTime | performedPeriod.start",
            "performer_name": "performer[0].actor.display",
            "reason": "@coding_display:reasonCode[0]",
        },
        "effective_date": ["performedDateTime", "performedPeriod.start"],
    },

    # --- CarePlan ---
    {
        "fhir_type": "CarePlan",
        "label": "CarePlan",
        "search_params": {},
        "table": "care_plans",
        "columns": {
            "title": "title",
            "status": "status",
            "intent": "intent",
            "category": "@coding_display:category[0]",
            "start_date": "period.start",
            "end_date": "period.end",
        },
        "effective_date": ["period.start"],
    },

    # --- Goal ---
    {
        "fhir_type": "Goal",
        "label": "Goal",
        "search_params": {},
        "table": "goals",
        "columns": {
            "description": "description.text",
            "lifecycle_status": "lifecycleStatus",
            "start_date": "startDate",
            "target_date": "target[0].dueDate",
        },
        "effective_date": ["startDate"],
    },
]
