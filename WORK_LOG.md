# Work log — 3 November 2026

## Local implementation

The supplied PLAN.md remains unchanged. Baseline `tests/test_all.py` passed before edits. Work stays in the new `pdf-to-pptx` repository on `feat/initial-service`; the AEO Articles repository is reference-only. No cloud, partner repository, paid AI or public fixture changes.

- P1–P4: vetted-IP connections with original hostname/TLS SNI, public embedded-address checks, safe input errors, bounded DNS workers and an overall async download deadline.
- P5–P6: strict ASCII signature/expiry parsing, Bearer fallback, atomic job records and a missing-record guard.
- P7: configurable page/download/pixel/output/time limits; conversion in a spawn child killed/joined at timeout, with input/result/scratch cleanup.
- P8–P9: existing scan/no-text warnings retained, font warning documented accurately.
- P10–P13: import-time missing-public-URL warning, MCP >=1.30,<2, request/admission cleanup, disk admission refusal, non-root image with health check and named-volume ownership.
- P14: Hilton character/background checks passed. Independent review found overlapping rotated glyphs could still be lost; a failing regression reproduced that. Unsafe overlaps now use a picture-only page and warning.
- P15–P16: dead MCP test code removed, real refused-tool and raw HTTP JSON-RPC tests added, deliberate transport Host-check choice documented.
- Additional confirmed fix: `/Rotate` needs transformed editable-line centres and shape rotation. A failing 90° test was recorded first; 90°/180°/270° checks now pass. Original line coordinates alone would accept misplaced text.
- Additional scratch collision fix: source `out.pdf` produces `out-converted.pptx`, so cleanup cannot delete the finished deck.

## Executed evidence

| Check | Result |
|---|---|
| Original expanded suite, Python 3.14 | OK: converter, safe downloader, web API and MCP tools |
| Hardening suite, Python 3.14 | 16 tests passed |
| Original expanded suite, Docker Python 3.12 | Same OK result |
| Hardening suite, Docker Python 3.12 | 16 tests passed |
| Real MCP SDK over loopback HTTP | Initialize, tools/list, refused conversion and job_status pass |
| Named-volume container smoke | Healthy, uid/gid 10001, volume owner 10001, health/auth/tool list/unknown job/REST admission/404 pass |
| Container log scan | Test token and `sig=` absent |
| Hilton local conversion | Actual source 20 pages; 20 slides; 618 text boxes; 1,335,138 bytes; 1.995 seconds on this Mac |
| Hilton positions, characters, redacted background | All passed, zero failures |
| LibreOffice inspection | Pages 1, 5 and 15 plus rotated fixture inspected; matching layout/images, substituted fonts |
| Synthetic slow fixture | 106 pages, calibrated 11.531 seconds on this Mac; not published |

Evidence lives under ignored `.quality/`: `before.json`, `rotation-before.log`, `redaction-before.log`, `all.log`, `hardening.log`, `docker-build.log`, `docker-all.log`, `docker-hardening.log`, `container-smoke.json`, and `hilton/`. Temporary Docker checks use unique containers/volumes and remove only their own resources. The pre-existing `churn-migration-db` container is untouched.

The before-fix record covers selected defects, rotation and overlapping redaction; it is **not** a complete per-defect red/green history. No claim of fully satisfying that historical T2 evidence requirement.

## Remaining gates

- Fresh-context independent review and any follow-up findings are recorded in `docs/review.md`.
- User must open the deck and click/edit text (acceptance row 5).
- Public fixture/live-link acceptance remains pending. The reference Hilton file is 20 pages despite PLAN.md's 21-page assumption.
- D1–D5 remain: hosting/project/domain, caller token model, GitHub location/name, confirm defaults, licence arrangement.
- Local quality checks are configured. Hosted setup is blocked because no GitHub origin is selected/authorized. Human gates remain enabled; no protected CI or authenticated hosted approval is claimed.
- Do not create GitHub resources, push, open a PR, merge, deploy, publish fixtures or spend money without the separate approval required by the supplied plan.

## Cloud Run choices and storage review — 3 November 2026

This entry supersedes the open D1–D5 decisions above. The owner selected Cloud Run, a new separate Google Cloud project, its provided HTTPS hostname, public source under AGPL in `EthanDev/pdf-to-pptx`, one shared API token and the tested conversion limits. Local deployment permission is already given; do not request it again as though missing. The owner specifically requested storage review before migration.

Created Google Cloud project `pdf-to-pptx-261103-7ec3` (number 497674931375) and public GitHub repository `EthanDev/pdf-to-pptx`. No Cloud Run runtime, database, queue, storage bucket or secrets have been created. Billing is not linked. Existing projects/resources are untouched. Added AGPL v3 licence and notice.

Independent review found seven design gaps around upload authority, cross-system publication, storage accounting, worker deadlines, logging, expiry and callback permissions. The reviewed proposal and selected resolutions are in `docs/cloud-run-storage-review.md`; 26 proposed evaluations are in `specs/cloud-run-storage/evaluations.json`. They are requirements, not passing cloud tests. No application behavior has changed in this phase.

The proposed cloud contract has direct Storage download URLs, accurate remaining expiry and conservative storage reservations, which can refuse work before the 20-job limit. These need the requested storage review before implementation. Hosted gate files were staged using the installed quality runtime; they are not protected/active approval enforcement yet. No PR has been opened or merged, and no deployment/paid AI job ran.

## Cloud storage implementation — 5 November 2026

Verified owner approval and merge of PR #1 at `550b2315f444e52e8adf2487fcaa2f2d3d317d2d`. Implemented the cloud backend, isolated worker, callbacks, deterministic queue/outbox, lease/retry and reservation protocols, exact-generation publication, expiry, deployment tooling and live proof runner. The original contract and 26 evaluation definitions remain unchanged.

Upload safety uses scoped cancellable resumable sessions with generation-zero preconditions and retained zero-byte fences. This implements the contract's required safe-path refinement; actual provider proof is still mandatory before deployment. Do not substitute mock storage tests for the held-upload/fence/resume check.

Validation: original converter/API suite and real MCP HTTP suite pass; 46 host regression cases pass. Python 3.12 image passes the same suites with one host/CI-only Git archive check skipped; that check passed on the host. Native Linux parent-death, watchdog and request-cancellation checks pass. Container is healthy, uid10001, named-volume ownership correct, and secret/signature log scan clear. Image/source parity was verified across27 code/dependency/licence files. Evidence is under `.quality/deployment/`.

Independent review caught and verified fixes for delayed signing extending expiry, dirty-source image attribution, interrupted secret setup, disabled-latest version selection, URL preservation on redeploy, and false-positive provider proof logic. Linux testing additionally caught/fixed legitimate PID1 parent handling. No approval fields or receipts were synthesized.

Google deployment remains blocked: `ethan@cashew.ai` requires reauthentication; the active personal account cannot access this project. The owner was asked to run `gcloud auth login ethan@cashew.ai`. No new billing link, cloud services, IAM, bucket, queue, database or secret mutations were performed during implementation. Existing deployment permission persists.

Workflow compatibility gap: the protected native SDLC audit does not recognize the external contract/manifest approval format used in PR #1. A separately reviewed compatibility change is required to recognize the real approval evidence; do not self-sign native wrapper fields or disable the gate. Branch protection is configured/read back with required tests, source approval and artifact checks; full release proof remains pending.

Local Linux verification used only a new task-owned Colima profile. Its internal-drive copy was removed after disk exhaustion; the working test VM/cache now live under `/Volumes/Main drive/.pptx-01a19b42`, with an ownership marker. Pre-existing caches/user data were preserved. No extra Git worktree or AEO/partner repository changes were made.
