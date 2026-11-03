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
