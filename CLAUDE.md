# Project rules

An Azure Function grounds a maintenance request with Cosmos DB records and calls a Microsoft Foundry
prompt agent. `README.md` describes the architecture, API and setup.

## Commands

- Tests: `python -m pytest tests/`. Bare `pytest` fails because the tests import `app` and
  `function_app` from the repository root.
- Matching evaluation: `python -m scripts.evaluate_matching`. It exits non-zero on any wrong link.
- Both run in CI on every pull request (`.github/workflows/ci.yml`) and need no Azure access.

## Rules

- **Infrastructure changes only through Terraform.** Never change Azure resources with `az` or the
  portal. Read-only `az` commands are fine.
- **Never run `terraform apply`, deploy the Function code, or create a Foundry agent version.**
  CI does these after approval in the `poc` environment, or the repository owner does them by hand.
- **`infra/bootstrap` is applied by hand by the repository owner.** It holds the Terraform state
  storage and the CI identities. For a change there, write the Terraform and give the exact
  commands to run. CI identities use OIDC, hold no stored secrets, and are each scoped to one job.
- **A single signal never links a machine.** Equipment matching needs an ID or serial number, or a
  model and customer together. A wrong link is worse than no link; the matching evaluation must
  stay at zero wrong links.
- **Prompt text is behavior.** The agent instructions, the grounding and history block headers and
  labels (`app/grounding/lookup.py`, `app/history/lookup.py`) and the retry message
  (`app/foundry/client.py`) all reach the model. Change them deliberately, in their own change.
  `tests/snapshots/` pins the full message; regenerate it with
  `UPDATE_SNAPSHOTS=1 python -m pytest tests/test_prompt_snapshots.py` and review the diff.
- **The agent definition lives in `agents/<name>/`** (`agent.yaml` and `instructions.md`), not in
  the Foundry portal. Until the release workflow is proven, `README.md` keeps a copy of the
  instructions and a test keeps the two identical.
- **The app always calls an exact agent version**, read from the generated, git-ignored
  `agent_reference.json`. Never add a fallback to the agent's latest version. For a local run,
  write the file with `python -m scripts.publish_agent --reference-only --version <n>`.
- **All data is sample data.** Keep seed data, test fixtures and evaluation cases that way.
- **Work on a branch and open a pull request.** Keep each one small enough to review.

## Known quirks

- Every Terraform plan for `infra/poc` shows `AzureWebJobsStorage` as an in-place change. That is
  expected (see the comment in `infra/poc/function.tf`); do not try to fix it.
