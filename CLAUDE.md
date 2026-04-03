# pi-sonar-agent Working Rules

- Solve exactly one Sonar issue per attempt.
- Stay inside the edit contract.
- Use the smallest patch that can pass validation.
- Prefer patch-style edits over whole-file rewrites.
- Do not make drive-by fixes in the same file.
- Record incidental findings in follow-ups instead of editing them now.
- If the correct fix needs broader refactoring, explain it in the review output instead of widening the patch silently.
