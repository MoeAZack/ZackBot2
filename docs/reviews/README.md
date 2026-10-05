# Reviews

One ticket at a time. Every ticket's evidence and every review lives here, next to the code it is about.

| File | Written by |
|---|---|
| `Txx_review_request.md` | Claude (implementation: changed files, evidence, risks, rollback, owner's checks) |
| `Txx_review_gpt.md` | GPT/Codex (findings P1-P3, verdict) |
| `Txx_fix_report.md` | Claude (answers to each finding, new evidence) |

**Workflow**
1. Claude implements a ticket on its branch (`t03-rollback-drill`, ...), adds `Txx_review_request.md`, and the owner pushes.
2. The owner runs the Windows checks named in the request and writes **ready** to both assistants.
3. GPT/Codex reviews the pushed branch and adds `Txx_review_gpt.md`; the owner pushes and writes **ready** again.
4. Claude answers in `Txx_fix_report.md` (new commits on the same branch) until the review has no open findings.
5. Accepted: the ROADMAP row gets ✅ with the date and the ticket enters `master` through a **pull request** that has
   passed the required checks (`verify fast`, `verify full`).

**Automatic checks (T04)**: GitHub Actions runs `verify fast` on every push and `verify full` on every pull request into
`master` (and on demand: Actions -> verify -> Run workflow). Results: the green/red mark next to each commit, and the
`verify-fast-*` / `verify-full-*` artifacts (summary JSON + evidence). `verify release` (rollback drill + running-bot
check) runs on the owner's PC: `verify_release.bat`.

**Protecting `master`** (owner, once, after the first green `verify full`): GitHub -> repository **Settings** ->
**Branches** -> **Add branch protection rule** (or **Rules -> Rulesets -> New branch ruleset**) for `master`:
- Require a pull request before merging (0 approvals is fine; the reviews live in this folder)
- Require status checks to pass: **verify fast** and **verify full**; require branches to be up to date
- Do not allow force pushes; do not allow deletions

Links: <https://github.com/MoeAZack/ZackBot2/tree/master/docs/reviews> (accepted) and the ticket branch's own `docs/reviews/`.
