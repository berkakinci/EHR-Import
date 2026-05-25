# Access Restrictions (OperationOutcome Codes)

Epic returns OperationOutcome issues alongside FHIR results to signal incomplete data.
These are captured in the `pull_warnings` table for forensic analysis.

## Warning Codes

Epic returns multiple `issue` entries within a single OperationOutcome response. The codes
work together:

- **4119 is a generic summary flag** — it accompanies one or more specific 59204/59205
  entries. It means "this response is incomplete" but doesn't say why on its own. The
  specific 59204/59205 sibling issues name exactly which sub-resource was withheld.
- On the USCDI v3 app with full endpoint registration, every 4119 has an accompanying
  59204 or 59205 that explains it. No standalone unexplained 4119s have been observed.

| Code | Level | Meaning |
|------|-------|---------|
| 4119 | Generic | "May not contain the entire record." Summary flag — always paired with a specific 59204 or 59205 that names the withheld sub-resource. |
| 59204 | App-level | "Client not authorized for [Resource] - [Sub-resource]." The **app registration** is missing a specific API endpoint. Affects all users equally regardless of login. Fix: register a new app with the missing endpoints (production-locked apps cannot be modified). |
| 59205 | User-level | "User not authorized for [Resource] - [Sub-resource]." The **authenticated user** lacks permission to view that sub-resource. Observed on proxy/guardian logins (blocked from "Outside Record" data) and on sandbox test users (blocked from specialty sub-resources like Genomics, SmartData Elements). Nothing the app developer can do. |
| 4101 | Informational | "Resource request returns no results." Normal — the patient simply has no data of that type. |
| 59100 | Informational | "Content invalid against the specification." Usually a parameter warning (e.g., unknown param ignored). |

## Observed Patterns

On production with the USCDI v3 app:

**App-level (59204) — non-USCDI sub-resources denied for all logins:**

| Resource | Denied sub-resources |
|----------|---------------------|
| Condition | Genomics, Dental Finding, Infection, Medical History, Reason For Visit |
| DocumentReference | Correspondences, Radiology Results, External CDAs, Document Information, Clinical References, Handoff, HIS, IRF-PAI, OASIS, Minimum Data Set |
| Procedure | Patient-Reported Surgical History, External Radiotherapy Summary |

**User-level (59205) — proxy/guardian blocked, direct login unaffected:**

| Resource | Proxy | Direct | What's blocked |
|----------|-------|--------|---------------|
| Medications | 15 | 131 | Outside Record (reported/external meds) |
| Allergies | 2 | 6 | Outside Record |
| Immunizations | — | 74 | Outside Record |
| MedicationDispense | — | 77 | Outside Record |
| Procedures | — | 27 | Outside Record |
| Goals | — | 0 | Outside Record |
| Social Hx | 0 | 1 | Outside Record + possibly sensitive categories |

**EHI vs FHIR data model differences (not access restrictions):**

| Resource | EHI | FHIR | Why the gap |
|----------|-----|------|-------------|
| Notes | 228 | 13 | EHI includes phone encounters (72), internal notes (104), non-USCDI sub-resources |
| Reports | 92 | 28 | EHI `ORDER_PROC` includes 75 lab orders → map to Observation, not DiagnosticReport |
| Encounters | 80 | 15 | EHI includes cancelled, phone, message encounters |
| Vitals | 93 | 52 | EHI includes screening questions, questionnaires, calculated fields |

## Known Limitations (USCDI v3 Distribution)

Epic's USCDI v3 automatic client distribution restricts which API endpoints patient-facing
apps can register. The following DocumentReference sub-resources are **not available** to
apps using USCDI v3 (or v1, or CMS Patient Access API) distribution — they only appear
under Distribution = "None", which requires manual IT coordination with each provider:

- **Correspondences** — MyChart messages between patient and provider
- **Radiology Results** — imaging reports
- **External CDAs** — imported C-CDA documents from other providers
- **Document Information** — scanned documents (consent forms, ECGs, etc.)
- **Clinical References** — reference documents

Additionally, some Condition sub-resources are non-USCDI:
- Genomics, Dental Finding, Infection, Medical History, Reason For Visit

**Workarounds attempted (all dead ends):**
- **Distribution: None** — unlocks full endpoint catalog but removes self-service activation.
  Each provider's IT team must manually request your client ID. Impractical for personal use.
- **Backend Systems audience** — unlocks full catalog but returns HTTP 500 on the OAuth
  authorize endpoint. Backend apps use `client_credentials` grant only (system-to-system,
  no interactive user login). Incompatible with patient-facing standalone launch.
- **USCDI v1 / CMS Patient Access API** — even more restrictive than USCDI v3.

The only reliable path to this data is Epic's EHI export ("Requested Record" under the
Cures Act) — see [ehi-import.md](ehi-import.md).

## FHIR vs EHI: What You Actually Get (Real-World Comparison)

To understand the practical coverage of the FHIR API, we compared three data sources for
an adolescent patient at a major children's hospital (May 2026). The proxy (guardian)
login has restrictions compared to the patient's own login — Epic limits guardian access
to certain data categories for minor patients (e.g., reproductive health, outside records):

1. **EHI export** — full Epic Clarity table dump via Cures Act "Requested Record"
2. **FHIR proxy pull** — guardian/parent login (proxy access)
3. **FHIR direct pull** — patient's own login

| Resource | EHI Export | FHIR (Proxy) | FHIR (Direct) | Notes |
|----------|-----------|-------------|---------------|-------|
| Labs | 120 | 120 | 120 | ✓ Perfect match |
| Reports | 17 | 28 | 28 | FHIR > EHI (includes outside record results) |
| Notes | 228 | 13 | 13 | Gap: non-USCDI sub-resources (see below) |
| Encounters | 80 | 13 | 15 | EHI includes non-clinical (phone, messages, cancelled) |
| Conditions | 17 | 33 | 33 | FHIR > EHI (one resource per encounter-diagnosis) |
| Allergies | 2 | 2 | 6 | Proxy blocked from outside record (59205) |
| Vitals | 93 | 52 | 52 | EHI includes screening questions + calculated fields |
| Medications | 15 | 15 | 131 | Proxy blocked from reported/outside meds (59205) |
| Social Hx | 2 | 0 | 1 | Proxy blocked entirely (59205) |
| Immunizations | — | — | 74 | Not in EHI comparison subset |
| MedicationDispense | — | — | 77 | Not in EHI comparison subset |
| Procedures | — | — | 27 | Not in EHI comparison subset |

**Key takeaways:**

- **Labs are complete.** FHIR returns every lab result the EHI has.
- **Proxy vs direct for minor patients.** The 59205 restriction blocks guardian logins from
  external/received records and sensitive categories (e.g., reproductive health). For data
  originating at this institution, proxy and direct logins return identical results.
  Impact: Medications 15→131, Allergies 2→6, Immunizations 0→74, Procedures 0→27.
- **The Notes gap is structural.** Of 228 notes in the EHI: ~30 are Clinical Notes (13
  returned via FHIR), 72 are telephone encounter documentation, 104 are internal/system
  notes, and the rest are in non-USCDI sub-resources (Correspondences = MyChart messages,
  Radiology Results, External CDAs, scanned documents).
- **Encounters/Vitals gaps are data model differences**, not access restrictions. EHI
  includes cancelled appointments, phone calls, screening questions, and calculated fields
  that FHIR correctly excludes from clinical resource types.
- **Reports and Conditions: FHIR returns MORE than EHI native counts** because it includes
  outside record data and creates one Condition resource per encounter-diagnosis instance.
