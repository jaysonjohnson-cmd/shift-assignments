# Shift Assignments & Triage

Client-only Next.js app that turns a Priority Page export into morning/afternoon shift assignments for a review team. Upload an `.xlsx` / `.csv`, pick today's reviewers, and each person gets their own queue.

## Local development

```bash
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

## Deploy

Push to GitHub and import the repo in Vercel. The framework preset is **Next.js**; no root directory override is needed (the Next.js app lives at the repo root).

## Notes

- Data stays in the browser — nothing is uploaded to a server.
- Reviewer names are managed on the `/settings` page and re-used as dropdown options in the crew picker.
- Morning priority slices auto-balance across the crew; they're editable per-reviewer.
- Unmatched afternoon rows always route to the **Overflow** bucket.
