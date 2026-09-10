---
name: new-release
description: Step-by-step guide for shipping a new version of SatLit. Use this skill whenever changes are ready to be released to users — it covers versioning, changelog, README review, and pushing to main.
---

# Release Skill

Follow these steps in order every time a new version is shipped.

---

## 1. Confirm the next version number

Read the current version:

```bash
cat VERSION
```

Ask the user: **"The current version is X.Y.Z — what should the next version be?"**

Wait for confirmation before proceeding. Do not assume a version bump on your own.

Use semantic versioning:
- **Patch** (X.Y.**Z**) — bug fixes, small tweaks, no new features
- **Minor** (X.**Y**.0) — new features, backward-compatible
- **Major** (**X**.0.0) — breaking changes or major redesigns

---

## 2. Review the README

Read `README.md` in full. Cross-reference it against all changes being released and ask yourself:

- Does the **Features** section reflect new or removed functionality?
- Does the **Datasets** table need a new row?
- Does the **Roadmap** have items that are now completed and should be checked off?
- Does the **Usage / Workflow** section still accurately describe the steps?
- Does the **Architecture** section still reflect the actual structure?

If any section is stale or missing coverage of the new changes, summarize exactly what you plan to change and ask for approval before editing. If nothing needs changing, say so and skip README edits.

---

## 3. Bump the VERSION file

Update `VERSION` to the confirmed version number (plain text, no extra whitespace):

```
0.2.0
```

---

## 4. Stage and commit

Stage only the files changed in this release (be explicit — avoid `git add .`):

```bash
git add VERSION <file1> <file2> ...
```

Write a commit message that:
- Starts with a conventional prefix: `feat:`, `fix:`, `refactor:`, `docs:`, or `release:`
- Clearly describes **what** changed and **why** in plain language
- Does **not** mention AI tools, assistants, or co-authors
- Uses the imperative mood ("add", "fix", "update" — not "added" or "adding")

Example:
```
feat: add auto-update system with one-click restart

Users who cloned the repo are now notified on startup when a new version
is available on main. A single button pulls the latest changes, reinstalls
dependencies, and restarts the server in place.
```

---

## 5. Merge into main

Ask for approval before merging. If approved:

```bash
git checkout main
git merge <working-branch> --no-ff -m "release: vX.Y.Z"
git push origin main
```

Stay on `main` — do not switch back to the working branch. Use `--no-ff` to keep the merge history readable.

Then ask if the working branch should be deleted. If yes:

```bash
git branch -d <working-branch>
```

---

## 6. Final checks

After pushing, verify:
- `VERSION` on `main` matches the intended version
- The update banner will correctly detect this as a new version for users still on the previous release (their local `HEAD` will differ from `origin/main`)
- The README reads accurately for a new user seeing the project for the first time
