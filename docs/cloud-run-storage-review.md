# Cloud Run storage review — 3 November 2026

Status: design ready for independent review, not implemented or deployed.

The owner selected Cloud Run, a separate Google Cloud project, the provided temporary HTTPS hostname, public AGPL source in EthanDev/pdf-to-pptx, one shared token and the existing conversion limits. These replace the earlier open deployment choices. The owner asked to review storage changes first.

## Finding

The local implementation cannot be deployed unchanged with reliable job/file retention. Its queue, job records and results live on one machine. Replacement instances would lose that data; overlapping instances/revisions can disagree about jobs and limits. A Cloud Storage directory mount is not a replacement for a database: Google explicitly documents no file locking for concurrent writes and incomplete POSIX semantics.

Sources checked today: [Cloud Run instance limits](https://docs.cloud.google.com/run/docs/configuring/max-instances-limits), [Cloud Storage mounts](https://docs.cloud.google.com/run/docs/configuring/services/cloud-storage-volume-mounts).

## Proposed change

| Component | Responsibility | Access |
|---|---|---|
| Public Cloud Run API/MCP | Existing three tools, Bearer auth, signed downloads, job admission and status | Only the new project's job database, bucket, queue and its own secret versions |
| Private Cloud Run worker | Fetch and convert one PDF per request, using the existing converter/time limits | No project IAM roles; invoked only by the queue's dedicated identity |
| Firestore | Durable job records, attempt leases, global admission/size counters and a dispatch outbox | API only; private database with no client access |
| Private Cloud Storage bucket | Finished PPTX objects under per-job/per-attempt names | API has narrowly scoped bucket access; worker gets a short-lived upload capability for one exact object |
| Cloud Tasks | Delivery and bounded retry of conversion requests | Queue permits at most two concurrent conversions, with twenty admitted jobs globally |
| Scheduled maintenance | Drain dispatch outbox, expire abandoned jobs and remove expired files/records | Dedicated identity; authenticated internal endpoint, no public maintenance action |

The API and worker have separate service accounts. The worker that reads untrusted PDF bytes retains the supplied plan's **no project roles** boundary. The coordinator necessarily needs limited new-project data/queue access; it gets no Owner/Editor role and no access to other projects. This is an explicit architectural revision of the plan's earlier single-process deployment, not a claim that the unchanged prototype is Cloud Run-ready.

## Flow and recovery

1. API transaction admits a job only if the global twenty-job limit and stored-data limit permit it. Store the job and dispatch outbox together. Limit counters cannot be per-instance variables.
2. Dispatcher creates a deterministically named task. A create timeout is ambiguous: retry with the same task name, never a new job ID. Scheduled retries handle a crash between saving the job and creating the task.
3. Queue invokes the private worker using Google-issued identity. Treat deliveries as at-least-once, not exactly-once.
4. Worker claims an attempt through the API using a random job-scoped capability. An atomic lease stops two attempts publishing simultaneously. Duplicate finished tasks return success without converting again. An active lease causes retry; an expired lease can be reclaimed.
5. Worker downloads through the existing vetted-IP downloader and runs the existing killable conversion child. Input and scratch files stay temporary; they are not uploaded or retained. Cloud Run termination can interrupt an attempt, so the queue can retry it.
6. A short-lived signed upload policy permits only the attempt's exact object name and at most 300,000,000 bytes. The worker uploads directly to Storage, then sends a small completion message. This avoids routing a 300 MB upload through Cloud Run's 32 MiB HTTP/1 request limit. Signing uses managed signBlob with narrowly scoped permission; no downloadable service-account key.
7. The API verifies the active attempt, stored object generation and size before atomically publishing completion and updating counters. Late callbacks and duplicate completions cannot replace the winner or decrement counters twice. Reads target the recorded object generation, never whichever upload happens to be newest.
8. Use the create-only attempt signer and API-owned final promotion selected below. Workers must be unable to overwrite final results; do not rely on a filename alone as isolation.
9. A successful completion is followed by task acknowledgement. If acknowledgement is lost, redelivery observes the terminal state. After a bounded retry window, abandoned jobs become failed and release capacity once; no permanent running/queued state.
10. Hide files immediately at the 24-hour expiry. Maintenance deletes objects and records and reconciles counters. Bucket lifecycle rules are a delayed safety net, not the exact expiry mechanism. In-progress downloads may finish; a new request after expiry must fail. Retained versions/soft-deleted bytes need an explicit bounded policy.

[Google's Cloud Tasks guidance](https://docs.cloud.google.com/run/docs/triggering/using-tasks) describes authenticated asynchronous delivery. [Cloud Run size limits](https://docs.cloud.google.com/run/quotas) distinguish HTTP/1 upload limits from streamed responses. [Storage signatures](https://docs.cloud.google.com/storage/docs/authentication/signatures) describe upload policy constraints.

## Compatibility and security

- Keep the three tool names, REST routes, failed-job/tool-error distinction and conversion defaults. The direct download URL, accurate expiry and accounted storage-admission revisions are listed explicitly below for owner review.
- Existing local-file mode and its tests remain available. Add a cloud backend rather than silently changing local recovery rules.
- Startup of a new API instance must never mark another instance's jobs failed. Lease/deadline recovery replaces local recover() in cloud mode.
- Public links remain protected by the existing HMAC route, or another reviewed equivalent preserving caller behavior. Stream large downloads; do not load a 300 MB result into API memory. Inline base64 remains capped at 3 MB.
- Never grant the public caller IAM or expose the bucket/database. Secrets remain in Secret Manager. Never log user source URLs, signed upload/download capabilities, task bodies, headers or callback tokens.
- Cloud Run platform request logs need explicit review too: signed queries can reach platform logs even when uvicorn access logs are off. Verification must inspect the deployed logging configuration, not merely application logs. Use a proven exclusion/redaction strategy or revise link encoding before launch.
- Expose a source-code link with the deployed release/commit and AGPL licence, providing the matching source for the running version. Public source must omit secrets, private fixture PDFs and local-only operational records.
- Use the generated run.app HTTPS address. No shared wildcard DNS dependency or custom DNS changes.
- No AEO/partner repository or existing cloud resource changes. No paid AI jobs.

## Required checks before deployment

See specs/cloud-run-storage/evaluations.json for the concrete failure and acceptance cases. Current local tests prove the converter and local service only; none yet proves this proposed cloud backend.

## Release boundary

Cloud infrastructure creation and AGPL publication are authorized, but implementation/release must still satisfy the project's recorded human/hosted gates. Repository protection and authenticated review are currently unconfigured. Do not disable human_gates or treat a local pass as hosted approval. Final storage review must resolve upload isolation, transaction boundaries, expiry, retry liveness and platform logging before application edits/release claims.

## Independent review and resolved design choices

A fresh-context reviewer identified seven gaps in the first draft. These are the selected fixes for the proposed implementation contract; they are not implemented/tested behavior yet.

1. **Upload isolation:** use a dedicated upload-signing identity with create-only access to the attempt prefix. The API may sign only for that identity, with no service-account private keys. Policy fixes exact object key, PPTX type, byte range and short expiry. Re-upload to an existing object must fail; prove it with real IAM tests. API alone promotes to a separate final prefix. A separate read-only signing identity can issue final-object downloads. Neither signer is attached to the PDF worker.
2. **Publication protocol:** upload immutable attempt object → verify exact generation and size → copy generation-pinned content to an API-owned final object using create-only destination preconditions → Firestore transaction rechecks the fencing token, attempt deadline and unpublished state, then publishes the exact final generation and adjusts counters once. Cloud Storage/Tasks operations stay outside transaction callbacks, which may be retried. A crash before the database commit leaves a tracked orphan. Cleanup must first prove an object is not referenced by a live/final record; publication and cleanup use a transaction-controlled object/reservation state. A crash after commit must preserve the winner.
3. **Storage accounting:** reserve worst-case staging, final-copy and retry bytes **before granting an upload capability**. Keep reservations until the capability has expired and objects are confirmed deleted or charged to completed storage. Queued records can remain small, but a claim lacking storage capacity fails plainly rather than waiting forever. Use one transactional admission/counter document, not distributed counter shards. The proposed cloud 2 GB setting is a stricter accounted ceiling than the local approximate admission threshold; transient duplicate copies and orphans count. This can refuse work before twenty active jobs. Make this behavior change explicit in docs and review.
4. **Deadlines:** set a 30-minute absolute job deadline at admission, a 300-second hard worker-attempt wall-clock budget, 600-second attempt lease and a 600-second HTTP/task dispatch timeout. The child conversion still has its own 120-second limit and download has 60 seconds. A process-group watchdog must kill the conversion tree before a lease can be reused; prove parent-death, native hangs and connection-timeout behavior on Linux. Retries use the same task name and at most three attempts, subject to the absolute deadline. Active-lease conflict returns retryable failure; terminal state returns successful acknowledgement; transient failure returns 503. Maintenance handles never-delivered tasks and exhaustion, not merely the queue's retry window. Queue concurrency alone is not proof of two running conversion processes.
5. **Logs/download links:** return short-lived **direct Cloud Storage signed GET URLs**, capped by the job's retention deadline. This prevents download signatures from entering Cloud Run request URLs at all. Disable optional Storage request/data-access logging and task execution-body logging where applicable, and verify effective inherited logging/export settings before launch. Do not claim that an exclusion prevents ingestion. The existing Bearer/HMAC file route can remain as a streamed compatibility route, with reviewed exclusions for any legacy signed-query requests; its guarantee is “not retained/exported,” not “never ingested.” No caller is directed to put a token in a URL path. This is a deliberate change from newly generated HMAC-to-API links; callers still receive a normal downloadable pptx_url.
6. **Expiry:** store one authoritative expires_at = completion + 24 hours. Enforce it in status, inline data and file reads, even with valid Bearer auth. Fresh URLs do not extend retention. Cap Storage signature expiry to this timestamp and return accurate remaining lifetime plus expires_at; do not always promise a new 24 hours near deletion. Stream legacy API downloads rather than redirecting to a longer-lived URL.
7. **Worker/callback boundaries:** separate job-claim capability from attempt-completion capability; scope each to one job/operation and deadline. Coordinator base URL is fixed configuration, never task-supplied. The worker never receives the shared API token or signing key. Public maintenance routes verify Google signature, issuer, expiry, exact audience and the allowlisted scheduler identity using the supported verifier; the API token or worker capabilities cannot invoke maintenance. Verify the worker has no effective project/bucket/secret roles, including inherited grants.

This design requires application/storage changes and new race/failure tests. It is **not ready for deployment** on the strength of the prototype's passing local tests. The owner-requested storage review is complete; the implementation contract and required source/hosted review path remain to be accepted before the cloud migration proceeds.


### Final clarification after re-review

- Count at most **three claimed conversion attempts**, independently of HTTP/task deliveries. Active-lease conflicts do not consume a conversion attempt. Configure task retry/backoff to allow recovery after a 600-second lease within the absolute 30-minute deadline; never use a three-delivery queue limit. The database deadline is authoritative even for never-delivered jobs.
- An upload that started before capability expiry can still be in flight. A policy expiry plus an empty listing is **not** evidence that its reservation can be released. Retain an uncertainty tombstone and reservation until terminal upload status or a proven immutable-object fencing mechanism prevents a late write. A live test must hold an upload open across expiry and cleanup, then attempt completion. If the selected Storage POST cannot provide that proof, change the upload path before deployment; do not invent a timing margin or silently reclaim capacity. Alert on unreconciled reservations rather than hide them.
- `links_expire_in` describes the **actual returned URL** lifetime; `expires_at` describes the job's 24-hour retention deadline. They may differ. Signed GET URLs include the recorded object generation and cannot outlive either the URL deadline or retention deadline. A fresh URL can be requested while retention remains.

The independent re-review found this concrete enough for owner review, with the above clarifications required in implementation. It did not approve or test a cloud backend. Runtime changes remain pending the owner-requested storage-contract review and the project's authenticated source-review setup.
