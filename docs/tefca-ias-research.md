# TEFCA IAS Research

## Summary

Investigated whether TEFCA Individual Access Services could provide access to
DocumentReference sub-resources (Correspondences, Radiology Results, External CDAs)
that are blocked by the USCDI v3 distribution on open.epic.com.

**Status:** Awaiting response from Epic Nexus (`QHINSupport@epic.com`), emailed 2026-06-27.

## Key Question

Does IAS client registration through Epic Nexus include non-USCDI DocumentReference
sub-resources, or is the data scope identical to USCDI v3 distribution?

The 4119/59204 restriction we hit is tied to which API endpoints are on the app
registration. On open.epic.com, USCDI v3 distribution limits the available catalog.
IAS registration goes through a different path (Epic Nexus staff, not self-service
portal) — it's unclear whether this grants broader endpoint access.

## What TEFCA IAS Is

TEFCA = Trusted Exchange Framework and Common Agreement (federal framework for
nationwide health data exchange). IAS = Individual Access Services (the patient-facing
use case — patients use an app to retrieve records from participating providers).

### Hierarchy

- **QHIN** (Qualified Health Information Network) — top-level network operator
  (Epic Nexus, CommonWell, eHealth Exchange, etc.). ~20 designated by the RCE.
- **Participant** — organization that contracts with a QHIN
- **Sub-participant** — connects through a Participant

### How it works with Epic

1. App developer joins a QHIN as Participant (or Sub-participant under someone like Fasten Health)
2. Gets an HCID (Home Community ID) in the RCE directory
3. Registers with Epic Nexus → gets a FHIR client ID distributed to all Epic Nexus Participants
4. Patient authenticates via standard SMART on FHIR OAuth2 (same MyChart login)
5. App makes FHIR R4 requests

### Epic Nexus registration details

- Contact: `QHINSupport@epic.com`
- They collect: client name, HCIDs, redirect URIs, JWK Set URLs
- No mention of selecting API endpoints from a catalog (unlike open.epic.com)
- No fee mentioned for this step
- Client ID synced to all Epic Nexus Participants automatically
- Source: https://open.epic.com/Home/Interoperate/TEFCA/IAS

### What Epic says about data scope

> "IAS apps will be authorized to call patient.$match and any USCDI v1 and v3 R4 FHIR APIs."

Ambiguous: could be a floor (minimum guaranteed) or a ceiling (maximum allowed).

## Barriers to Personal Use

| Barrier | Detail |
|---------|--------|
| Must be a TEFCA Participant | Contract with a QHIN required. Cannot participate as individual (unverified). |
| IAL2 identity proofing | Patient must verify via CLEAR or ID.me (gov ID + biometrics). Per IAS SOP v2.1. |
| Confidential client only | Must maintain a JWK Set URL. Already have this. |
| Organizational overhead | Privacy policy review, legal compliance, HCID directory registration. |
| QHIN cost | Unknown — QHINs may charge participants. Epic Nexus step itself appears free. |

## Potential Outcomes

1. **IAS grants broader access** → worth pursuing QHIN membership to unlock Correspondences/Radiology
2. **Same USCDI v3 scope** → no benefit over current setup; TEFCA only useful for multi-org convenience
3. **Broader but not the specific sub-resources we need** → partial win, evaluate case-by-case

## MyChart Central (Late 2026)

Epic is adding "community" scope to IAS — patient logs into MyChart Central once
and authorizes sharing from all linked organizations. Returns subject tokens for
each org, exchangeable for access tokens. Single auth flow, multi-provider pull.
Same data scope question applies.

## Sources

- https://open.epic.com/Home/Interoperate/TEFCA/IAS (Epic Nexus IAS documentation)
- https://www.epic.com/tefca (participant list — 335 orgs live, 2102 hospitals)
- https://docs.connect.fastenhealth.com/guides/tefca-ias (Fasten Health developer guide)
- https://rce.sequoiaproject.org/participate/ (QHIN/Participant/Sub-participant structure)
- https://fastenhealth.mintlify.app/guides/tefca-subparticipant (sub-participant onboarding example)
