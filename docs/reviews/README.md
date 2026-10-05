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
5. Accepted: the ROADMAP row gets ✅ with the date and `master` is fast-forwarded to the ticket branch.

Links: <https://github.com/MoeAZack/ZackBot2/tree/master/docs/reviews> (accepted) and the ticket branch's own `docs/reviews/`.
