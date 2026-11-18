# Project rules

An Azure Function grounds a maintenance request with Cosmos DB records and calls a Microsoft Foundry
prompt agent. `README.md` describes the architecture, API and setup.

## Commands

- Tests: `python -m pytest tests/`. Bare `pytest` fails because the tests import `app` and
  `function_app` from the repository root.
- Matching evaluation: `python -m scripts.evaluate_matching`. It exits non-zero on any wrong link.
- Both run in CI on every pull request (`.github/workflows/ci.yml`) and need no Azure access.
- Agent evaluation: `python -m scripts.evaluate_agent --version <n>`. It calls the live agent, so it
  needs `az login` and is not part of the pull request checks.
- Merging to `main` starts a release (`.github/workflows/release.yml`) when application, agent,
  script or `infra/poc` files changed. `README.md`, "Release", describes it and the rollback.
- A release whose `agents/<name>/agent.yaml` or `instructions.md` differs from the newest GitHub
  Release evaluates its agent version three times and reviews the runs. `README.md`, "When the agent
  definition changes", has the rules.

## Rules

- **Infrastructure changes only through Terraform.** Never change Azure resources with `az` or the
  portal. Read-only `az` commands are fine.
- **Never run `terraform apply`, deploy the Function code, or create a Foundry agent version.**
  CI does these after approval in the `poc` environment, or the repository owner does them by hand.
  `python -m scripts.publish_agent --create` is for the release workflow only.
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
  the Foundry portal. A release creates each agent version from those two files.
- **The app always calls an exact agent version**, read from the generated, git-ignored
  `agent_reference.json`. Never add a fallback to the agent's latest version. For a local run,
  write the file with `python -m scripts.publish_agent --reference-only --version <n>`.
- **An evaluation assertion is `blocking` only if the live agent version passes it reliably.**
  Anything else goes under `advisory` in the case's `expect.yaml`, with a comment saying why.
  The schema check always blocks; a case may only narrow it with `except_null` for a named field.
- **All data is sample data.** Keep seed data, test fixtures and evaluation cases that way.
- **Work on a branch and open a pull request.** Keep each one small enough to review.

## Known quirks

- Every Terraform plan for `infra/poc` shows `AzureWebJobsStorage` as an in-place change. That is
  expected (see the comment in `infra/poc/function.tf`); do not try to fix it. The cause is in the
  `azurerm` provider (checked in 5.14.0's source; related upstream issue
  hashicorp/terraform-provider-azurerm#30732): it writes a connection string with an empty key on
  every create and update unless the configuration sets the value, and it leaves the setting out of
  the app settings it reads back. Deleting the line or adding `ignore_changes` would make the next
  update write that keyless string over the identity-based setting.
