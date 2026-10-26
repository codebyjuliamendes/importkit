# ImportKit

ImportKit is an embeddable CSV onboarding workflow for SaaS products. Map external CSV headers to contact fields, validate rows, preview results, commit valid contacts, download an error report, and reverse a committed batch.

## Run locally

Requires Node.js 20 or newer.

```bash
npm install
npm start
```

Open <http://localhost:3006>. The first run prepares a fictional sample preview. SQLite data lives in `data/importkit.sqlite` and is ignored by Git.

## Try the workflow

1. Open **New import** and choose a CSV file or paste rows.
2. Inspect valid and invalid rows and their error messages.
3. Commit valid rows. Invalid rows are never inserted.
4. Use **Undo this import** to remove only records created by that batch.

Name and email are required; company is optional. The import dialog maps columns such as `Full Name`, `E-mail`, and `Organization` to those fields. The demo is capped at 800 KB per upload.

## Design notes

- Preview records are stored separately from customer contacts.
- Email uniqueness is enforced by SQLite and rechecked when a batch commits, so concurrent imports cannot silently create duplicate contacts.
- The commit path is transactional; a batch is either committed consistently or rolled back.
- Revert is scoped by `import_id` and never deletes contacts from another batch.
- The sample app stores fictional data locally. A production service would need tenant isolation, configurable schemas, access controls, encryption, retention rules, and malware scanning.

## API

- `GET /api/dashboard` — workspace metrics and recent imports.
- `GET /api/imports/:id/errors.csv` — download invalid rows and their validation errors.
- `GET /api/contacts` — imported contacts.
- `POST /api/imports/preview` — validate CSV and save a reviewable preview.
- `GET /api/imports/:id` — inspect row results.
- `POST /api/imports/:id/commit` — commit valid rows.
- `POST /api/imports/:id/revert` — remove that batch's contacts.

## Product direction

Ship an open-source React or web-component uploader and a hosted validation API. Charge by monthly imported rows or retain a generous free tier for small teams.
