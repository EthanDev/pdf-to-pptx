# Cloud Run storage implementation — 5 November 2026

Status: implementation undergoing verification. No Cloud Run deployment or live cloud acceptance is claimed.

The owner approved and merged [contract PR #1](https://github.com/EthanDev/pdf-to-pptx/pull/1), merge `550b2315f444e52e8adf2487fcaa2f2d3d317d2d`. Implementation and deployment are explicitly authorized. The original reviewed contract and 26 evaluation definitions remain unchanged.

## Implemented

- `app/cloud_state.py`: durable admission/outbox, global twenty-job/two-running limits, per-attempt leases, three claimed attempts, 30-minute job deadline, storage reservations, generation-pinned publication, cleanup and expiry. Transactions perform no file/queue/network side effects.
- `app/cloud_google.py`: Firestore transactions, deterministic Cloud Tasks IDs, private Cloud Storage, create-once uploads, immutable final promotion, streamed reads and managed download signing.
- `app/cloud_routes.py`: operation/job-scoped callback credentials and separately verified Google maintenance identity. Worker receives no shared API token or project roles.
- `app/cloud_worker.py`: private task worker, fixed coordinator address, 300-second process-group watchdog, killable 120-second conversion, explicit parent-death protection and supervisor-owned scratch cleanup.
- Local-file mode remains supported. The three MCP tools and REST routes remain. Cloud results use direct generation-pinned Storage URLs capped to remaining retention. Existing signed/Bearer file route streams rather than buffering whole decks.
- `deploy/gcp.py`: isolated project setup, IAM, numeric secret versions, frozen source archives, immutable image digests, URL-preserving redeployment and authenticated maintenance scheduling. Runtime dependencies are locked for the Python 3.12 image/CI.

## Upload protocol refinement

The reviewed contract explicitly requires changing the upload path if an expiring signed POST cannot prove late-upload safety. This implementation uses a server-created resumable session with exact size and `ifGenerationMatch=0`, restricted to one attempt object. It is a scoped upload capability, not a service-account key.

Cleanup cancels known sessions and atomically replaces the attempt/final staging name with a zero-byte fencing object. A late generation-zero upload/copy must then fail its precondition. Fences live eight days, longer than Storage's documented seven-day resumable session lifetime. Reservations are not released until fencing succeeds. Unknown session URLs still target the known attempt object and are fenced the same way. Published final objects are preserved until retention expires.

This provider behavior must be demonstrated by the live held-partial-upload/fence/resume and create-once tests. An unrelated 400, transient 429/5xx or unfinished 308 is **not** accepted as evidence. Failure of the live proof blocks deployment.

Sources: [Storage resumable session lifetime/cancellation](https://docs.cloud.google.com/storage/docs/resumable-uploads), [generation preconditions](https://docs.cloud.google.com/storage/docs/request-preconditions).

## Verification boundaries

Offline tests exercise shared state/restart, concurrent admission, duplicate delivery, stale callbacks, partial publication, outbox repair, retries, reservations, expiry, capability scope and sanitized HTTP failures. Deployment regressions cover dirty-source refusal/digest pinning, incomplete secret setup, disabled latest versions, URL-preserving redeployment and denied lookup safety. Linux process tests exercise native child death, hard watchdog and cancelled HTTP work.

These tests do not establish live Google IAM, storage, logging or Cloud Run behavior. The original `evaluations.json` remains a requirements file. Evidence logs under ignored `.quality/deployment/` distinguish host, image/Linux and live runs.

Cloud sign-in is currently blocked: the project owner login `ethan@cashew.ai` requires reauthentication. The active personal account does not have project access. No alternate identity or permission bypass was attempted. Refresh the owner login using `gcloud auth login ethan@cashew.ai`; never paste tokens or passwords into the chat.

## Deployment sequence

From this repository:

```sh
python3 deploy/gcp.py prepare
# Commit and publish the verified source; builds refuse a dirty checkout.
python3 deploy/gcp.py build
python3 deploy/check_live.py storage
python3 deploy/gcp.py deploy
python3 deploy/check_live.py mcp
```

The generated `run.app` service URL is recorded in `.quality/deployment/cloud-resources.json`. API credentials are stored in the new project's `pptx-api-token` secret; signing material uses `pptx-link-key`. Values are never printed. The runtime worker has neither secret.

Before release, additionally verify zero effective worker roles, private bucket/disabled soft delete/versioning, task retry/log configuration, inherited log sinks/audits, large streamed results, instance replacement and cleanup races. An HTTPS health monitor remains a release check. Do not claim the complete 26-case platform acceptance from a successful small-PDF smoke test.

## Rollback

Preserve the data bucket, database, secrets and maintenance schedule. Revert API/worker traffic only to a **cloud-backend-compatible** reviewed digest. The earlier local-disk image is not a compatible rollback target. For the first release, stop new admissions/queue dispatch and remove public API invoker access if necessary while retaining data for diagnosis. The existing AEO service and its project resources are outside this deployment.
