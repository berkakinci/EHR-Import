# eClinicalWorks / healow Integration Plan

## Overview

eClinicalWorks (eCW) is a non-Epic EHR serving 180,000+ physicians. Patient-facing FHIR access goes through the **healow** platform. The first target practice uses portal `mycw17.eclinicalweb.com/portal1014/` with practice code **JECCAA**.

Unlike Epic (centralized open.epic.com registration, Brands Bundle discovery, auto-distribution for public apps), eCW uses a practice-scoped model where each practice is a separate FHIR endpoint identified by a practice code in the URL path.

---

## What We Know

### FHIR Endpoint (confirmed working)

| Field | Value |
|-------|-------|
| FHIR base URL | `https://fhir4.healow.com/fhir/r4/JECCAA` |
| Authorization endpoint | `https://oauthserver.eclinicalworks.com/oauth/oauth2/authorize` |
| Token endpoint | `https://oauthserver.eclinicalworks.com/oauth/oauth2/token` |
| FHIR version | R4 (4.0.1) |
| US Core | Yes (CapabilityStatement confirms us-core-server) |
| Metadata endpoint | Responds with full CapabilityStatement |

### Authentication

| Feature | eClinicalWorks/healow | Epic (for comparison) |
|---------|----------------------|----------------------|
| Developer portal | connect4.healow.com | open.epic.com |
| Patient login | healow patient portal | MyChart |
| Supported auth | Public (PKCE), Confidential-symmetric, **Confidential-asymmetric** | Public (PKCE), Confidential (JWT or secret) |
| JWT signing algorithm | **RS384** (only) | RS384 |
| JWKS delivery | App hosts `/.well-known/jwks.json`, healow fetches it | JWKS uploaded to open.epic.com during registration |
| Token lifetime | Access: 1 hour, Refresh: 90 days | Access: 1 hour, Refresh: rolling |
| Offline access | `offline_access` scope (confidential only) | `offline_access` scope |
| CORS | **Not supported** — must be server-side | Not supported |
| App distribution | Practice opts in using your client ID | Auto-distributed (public) or per-org activation (confidential) |

### Available Resources (from SMART config scopes)

Patient-facing read scopes confirmed available:
- Patient, Observation, DiagnosticReport, DocumentReference, Encounter
- Condition, AllergyIntolerance, MedicationRequest, MedicationDispense, MedicationAdministration
- Immunization, Procedure, CarePlan, CareTeam, Goal, Device
- Coverage, Claim, ServiceRequest, Specimen, Media, Binary
- Practitioner, PractitionerRole, Organization, Location, RelatedPerson
- QuestionnaireResponse, Questionnaire, FamilyMemberHistory, Provenance
- **ChargeItem** (not available in Epic)
- **Basic** (not available in Epic)

### Key Differences from Epic

1. **Practice-scoped URLs** — practice code is part of the FHIR base URL path (`/fhir/r4/JECCAA`), not a separate provider config
2. **No Brands Bundle** — discovery is via the healow Clinical FHIR Endpoints page or known practice codes
3. **JWKS hosting required** — you must serve your public key at a URL healow can fetch (vs uploading to Epic)
4. **Separate sandbox/production apps** — credentials are cleanly separated, not a toggle
5. **No sub-resource restrictions (4119/59204/59205)** — eCW doesn't use Epic's restriction model
6. **Rate limit** — 250 calls/minute per practice code (vs Epic's undocumented but generous limits)
7. **Patient consent model** — patient must authorize via healow portal; data withheld until they do

---

## TODO — Registration & Setup

### 1. Register on healow Developer Portal
- [ ] Go to [connect4.healow.com/apps/jsp/dev/signIn.jsp](https://connect4.healow.com/apps/jsp/dev/signIn.jsp)
- [ ] Click "Sign Up" and complete 4-step form (Contact, Company, Other details, Security)
- [ ] Verify email and set password
- [ ] Sign in to developer console

### 2. Register Sandbox Clinical App
- [ ] Click "Register Clinical App" in developer console
- [ ] Set launch type: **Patient-centric** (standalone)
- [ ] Set authentication: **Asymmetric (JWKS)** — confidential
- [ ] Select scopes: tick "All" for patient-facing read scopes + `offline_access`
- [ ] Add redirect URI: `https://localhost:9432/callback`
- [ ] Configure JWKS URL (see step 3)
- [ ] Complete questionnaire and submit
- [ ] Note sandbox client ID

### 3. JWKS Hosting
- [ ] Generate RSA key pair (or reuse existing if compatible with RS384)
- [ ] Host public JWKS at a reachable HTTPS URL
- [ ] Options:
  - GitHub Pages (already enabled for EHR-Import repo)
  - Static file on any HTTPS host
  - Dedicated endpoint in the auth server
- [ ] Register JWKS URL with healow

### 4. Test in Sandbox
- [ ] Sandbox practice code: **JAFJCD**
- [ ] Sandbox FHIR base: `https://fhir4.healow.com/fhir/r4/JAFJCD`
- [ ] Test patients (password: `e@CWFHIR1`): AdultFemaleFHIR, AdultMaleFHIR, ChildFemaleFHIR, ChildMaleFHIR
- [ ] Confirm OAuth flow works with RS384 client_assertion
- [ ] Confirm token exchange returns access + refresh tokens
- [ ] Confirm FHIR resource reads work

### 5. Register Production App
- [ ] Register a second Clinical App with Environment: Production
- [ ] Use production redirect URI and JWKS URL
- [ ] Note production client ID (separate from sandbox)

### 6. Connect Practice JECCAA
- [ ] Provide practice with production client ID
- [ ] Or: patient finds app in healow App Gallery
- [ ] Confirm FHIR base `https://fhir4.healow.com/fhir/r4/JECCAA` works with production credentials

---

## Implementation Plan

### Phase 1: Auth Abstraction

The current auth module (`ehr_import/auth.py`) is Epic-specific. Refactor to support multiple EHR backends.

**Changes needed:**
- Extract Epic-specific OAuth logic into an `EpicAuth` class/module
- Create `HealowAuth` class/module with:
  - RS384 `client_assertion` JWT generation (different from Epic's — `aud` is the token endpoint, not the FHIR base)
  - Token exchange via `https://oauthserver.eclinicalworks.com/oauth/oauth2/token`
  - Refresh token handling (90-day fixed expiry vs Epic's rolling)
- Auth method selection based on provider config (`ehr_type: "epic"` vs `ehr_type: "ecw"`)

**Key JWT differences:**
```
# Epic client_assertion
{
  "iss": "<client_id>",
  "sub": "<client_id>",
  "aud": "<token_endpoint>",
  "jti": "<unique_id>",
  "exp": <now + 5min>
}
# Header: {"alg": "RS384", "kid": "<kid>", "typ": "JWT"}

# healow client_assertion (same structure, but alg MUST be RS384)
# Same fields, aud = token endpoint
# JWKS must be hosted at a URL healow can fetch
```

### Phase 2: Config & Discovery

**config.json changes:**
```json
{
  "providers": {
    "Andover Pedi (eCW)": {
      "ehr_type": "ecw",
      "practice_code": "JECCAA",
      "fhir_base": "https://fhir4.healow.com/fhir/r4/JECCAA",
      "portal_url": "https://mycw17.eclinicalweb.com/portal1014/"
    }
  }
}
```

**Discovery changes:**
- Epic providers: existing Brands Bundle flow (unchanged)
- eCW providers: SMART config fetch from known `fhir_base` (practice code already determines the URL)
- No equivalent of Brands Bundle search for eCW — practice codes are known upfront

### Phase 3: Pull Adaptation

The pull logic (`ehr_import/pull.py`, `ehr_import/resources.py`) should mostly work as-is since both systems speak FHIR R4. Potential issues:

- **Search parameters** — eCW may support different search params than Epic
- **Pagination** — verify Bundle pagination works the same way
- **Binary content** — eCW may handle DocumentReference attachments differently
- **No OperationOutcome warnings** — eCW won't return Epic's 4119/59204/59205 codes; need to handle eCW-specific error patterns
- **Category-scoped Observations** — eCW's SMART config lists category-specific scopes; may need to query by category

### Phase 4: Testing & Validation

1. Sandbox end-to-end: auth → pull all resource types → store in DB
2. Compare resource structure with Epic pulls (field mapping differences)
3. Production auth with practice JECCAA
4. Full production pull and data validation

---

## Resources & Links

| Resource | URL |
|----------|-----|
| healow Developer Portal (registration) | https://connect4.healow.com/apps/jsp/dev/signIn.jsp |
| healow Clinical FHIR Endpoints (search) | https://connect4.healow.com/apps/jsp/dev/r4/fhirEndpoints.jsp |
| healow FHIR Documentation | https://connect4.healow.com/apps/jsp/dev/r4/fhirClinicalDocumentation.jsp |
| eCW Provider-centric portal (not needed for patient apps) | https://fhir.eclinicalworks.com/ecwopendev/ |
| Medblocks eCW Integration Guide (third-party, detailed) | https://medblocks.com/docs/ecw-integration-guide |
| Practice JECCAA SMART config | https://fhir4.healow.com/fhir/r4/JECCAA/.well-known/smart-configuration |
| Practice JECCAA CapabilityStatement | https://fhir4.healow.com/fhir/r4/JECCAA/metadata |
| Sandbox practice code | JAFJCD |
| Sandbox test credentials | Username: AdultFemaleFHIR / Password: e@CWFHIR1 |

---

## Open Questions

1. **JWKS hosting** — Can we use GitHub Pages (`berkakinci.github.io/EHR-Import/.well-known/jwks.json`)? Or do we need a separate host? healow requires HTTPS and public reachability.
2. **Key reuse** — Our existing RSA key is used for Epic JWT auth. Can we reuse it for healow (both use RS384), or should we generate a separate key pair?
3. **Practice opt-in** — Does the practice need to do anything on their end, or does the patient authorizing via healow portal suffice?
4. **Rate limits** — 250 req/min is tight for a full pull. May need to add rate limiting/backoff to the client.
5. **Multiple eCW practices** — If we add more eCW providers later, each is a separate practice code and potentially separate FHIR base URL. The config model handles this naturally.
