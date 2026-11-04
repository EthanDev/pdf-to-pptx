# Cloud Run storage contract review

On 4 November 2026, the owner approved the storage contract at commit `52e0671e554e415ba454341cdbd520b943d5de2f` and authorized opening its review PR. Deployment permission is already given. This record preserves that decision; it is not an authenticated GitHub review or a signed receipt.

Review the unchanged [storage contract](cloud-run-storage-review.md) and its [26 proposed evaluations](../specs/cloud-run-storage/evaluations.json). The [manifest](reviews/cloud-run-storage-manifest.json) binds their exact bytes to the approved source commit. This PR adds the decision/manifest only; application code is unchanged.

The approved changes are durable job records/files, queued retries with leases, a PDF worker without project roles, isolated uploads/final results, direct Storage download links, accurate expiry and conservative storage reservations. Upload/cleanup races and the other evaluations still need implementation and proof.

A trusted human must review this PR in GitHub. The source-review workflow must reject a missing approval and pass only after a real current-commit approval. Branch protection/bootstrap and application migration remain pending; no Cloud Run runtime has been deployed. No merge is authorized by this record.
