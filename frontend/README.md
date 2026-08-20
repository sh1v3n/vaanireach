# frontend/

**Not a working dashboard yet — scaffold only.** See
[`../docs/architecture.md`](../docs/architecture.md) for the intended
Human Review Dashboard design (Source pane / Generated Content pane /
Verification pane / Approve-Reject-Regenerate-Edit actions).

## Run locally (no Docker required)

```bash
cd frontend
npm install
npm run dev
```

This currently serves a single placeholder page stating the project is in
its architecture phase. Type-checking works (`npx tsc --noEmit`); there is
no working UI to build/test yet.
