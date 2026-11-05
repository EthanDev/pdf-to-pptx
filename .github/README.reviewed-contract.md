# Reviewed-contract compatibility

The storage contract was approved and merged in PR #1, but the original artifact check only recognizes native intent/spec/plan folders. This adapter recognizes that existing approval without creating signed-off fields or changing the approved files.

Only PRs containing the exact line `Contract: cloud-run-storage` use this path. The checker, configuration, manifest and reviewer policy must come from the protected base branch. The bootstrap PR therefore runs the original native audit; the adapter becomes usable only after a human reviews and merges it.

The check reads GitHub's actual merged PR and review records. It requires the pinned review commit, an allowed human reviewer, exact unchanged contract/evaluation bytes in the base, candidate and reviewed commit, and a limited set of changed paths. It rejects bots, dismissed/revoked approvals, changed contracts, disabled human gates and changes to the approval/checker guards. Unmarked PRs continue using the native artifact audit.

The separate current-commit human approval check remains required. This adapter does not approve application code, merge a PR, grant cloud permissions or authorize deployment. The project-wide `human_gates` setting remains enabled. It does not change the installed global Codex runtime or any other repository.

Six offline negative/positive tests cover these boundaries. Live GitHub use must be verified after the compatibility PR is human-merged; a unit test is not hosted enforcement evidence.
