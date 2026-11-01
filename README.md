# HR Assistant Chatbot

An HR assistant for employees. It answers questions about leave, benefits, payroll, conduct and other policies, using your organisation's HR documents, and it cites the policies it used.

- **Backend:** Python 3.13, FastAPI, Google Gemini (`google-genai` SDK)
- **Frontend:** React 19, TypeScript, Vite

```
Browser ──► nginx (Frontend, :8080) ──/api──► FastAPI (Backend, :8000) ──► Gemini API
                                                   │
                                                   └── data/policies/*.md (retrieval index)
```

## Features

| Area | What's included |
|---|---|
| Grounded answers | Retrieval-augmented generation over `Backend/data/policies/*.md`, using Gemini embeddings and an on-disk cache. Falls back to keyword search if embeddings are unavailable. Answers cite their sources. |
| Answer cache | Repeated standalone questions are answered without calling Gemini: in about 0.5s instead of 5s, using no quota. Reworded questions match when their meaning is very similar *and* they map to the same policy sections. Editing a policy clears the cache. |
| Conversation memory | Follow-up questions ("and how many can I carry forward?") are searched together with the previous question. History is capped by `MAX_HISTORY_CHARS` to bound token cost. |
| HR insights | Questions the policies don't cover are logged, and so is 👍/👎 feedback with an optional comment. HR reviews them at `GET /api/v1/admin/insights` to find gaps in the policy documentation. No employee identity is stored, sensitive data is never stored, and records are purged after `INSIGHTS_RETENTION_DAYS`. |
| Escalation | An **Email HR** link on every answer opens an email to `HR_CONTACT_EMAIL` with the question pre-filled. |
| Previous chats | A sidebar of past conversations (Today / Yesterday / Previous 7 days / Older), which you can switch between or delete. Chats are saved **only in the employee's browser** for 30 days (up to 50). Messages with sensitive data are saved redacted. |
| Policy documents | The **📄 Documents** panel lists every document the bot answers from. Anyone can open them, and answer citations link to them (PDF citations open at the cited page). HR admins unlock upload and delete with `ADMIN_API_TOKEN`. Upload accepts **PDF, Word (.docx), Markdown and text**. Changes take effect immediately, and only new or changed sections are re-embedded. |
| Chat attachments | Employees can attach their **own** files with 📎, drag-and-drop or paste: images (PNG, JPEG, WebP), PDFs (including scanned ones), Word and text files. They can then ask about the file, for example "does my offer letter match policy?". Attachments are private to the conversation, never indexed, can't be downloaded back, and are deleted after `ATTACHMENT_TTL_MINUTES`. **Every file is checked for sensitive data before Gemini sees it.** Images and scanned PDFs are read with local OCR (RapidOCR) for this check, so an ID-card photo is rejected without leaving the server. Image metadata (EXIF/GPS) is stripped. |
| Streaming | Token streaming over Server-Sent Events, with a Stop button and handling for client disconnects |
| Sensitive data guard | Messages containing SSN, Aadhaar, PAN, passport, driver's licence or card numbers, or medical records are **blocked before they reach Gemini**. They are not logged, and they are redacted in the UI. |
| Reliability | Retries with exponential backoff for 429/5xx and network errors, request timeouts, friendly error messages, retry from the UI |
| Abuse protection | Per-IP rate limiting (with optional Redis for multiple replicas), message-size limits, history caps |
| Security | Security headers and a strict CSP in nginx. Raw HTML from model output is not rendered. Secrets come from the environment only. `/docs` is disabled in production. Containers run as non-root. |
| Observability | JSON logs with request IDs (`X-Request-ID`) that never include message content. `/healthz` (liveness) and `/readyz` (Gemini reachability and index status) endpoints. |
| Privacy | Conversations are kept in `sessionStorage` only and cleared when the tab closes. The server is stateless. |
| Tests | `pytest` for the backend (offline, using a fake LLM) and `vitest` for the frontend |

---

## 1. Get a Gemini API key

> **Important:** a consumer **Gemini Advanced / Google One AI Premium** subscription (the one used in the Gemini app) **does not include API access**. Apps like this one use the **Gemini API**, which is billed separately through Google AI Studio or Google Cloud.

1. Go to **https://aistudio.google.com/apikey** and sign in with your Google account.
2. Click **Create API key**. Choose an existing Google Cloud project or let it create one.
3. Copy the key. It starts with `AIza...`.
4. *(Strongly recommended for HR data)* In AI Studio, open **Billing** and link a billing account to switch the project to the **paid tier**. On the **free tier, Google may use your prompts and responses to improve its products**. On the paid tier it does not. Check Google's current [Gemini API terms](https://ai.google.dev/gemini-api/terms) to confirm.
5. *(Recommended)* In Google Cloud Console → *APIs & Services* → *Credentials*, **restrict the key** to the "Generative Language API" and set a budget alert.

All the app needs is that key in `GEMINI_API_KEY`. No other credentials are required.

> Before you use a personal Google account or key for real employee data, check with your IT, security or data-protection team. Company HR data may need to go through an organisation-owned Google Cloud project, such as Vertex AI, with a data processing agreement.

---

## 2. Run locally (development)

### Backend

```powershell
cd Backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
copy .env.example .env        # then edit .env and set GEMINI_API_KEY
uvicorn app.main:app --reload --port 8000
```

- API docs: http://localhost:8000/docs
- Readiness: http://localhost:8000/readyz should show `"gemini_reachable": true` and `"retrieval_mode": "embeddings"`

### Frontend

```powershell
cd Frontend
npm install
npm run dev
```

Open http://localhost:5173. The Vite dev server proxies `/api` to `localhost:8000`.

### Tests

```powershell
cd Backend;  pytest
cd Frontend; npm test; npm run typecheck
```

---

## 3. Run in production (Docker)

```powershell
copy Backend\.env.example Backend\.env    # set GEMINI_API_KEY, COMPANY_NAME, HR_CONTACT_EMAIL
docker compose up --build -d
```

Open http://localhost:8080. nginx serves the React build and proxies `/api` to the backend, with buffering disabled so streaming works.

### Production checklist

- [ ] **Authentication.** The app ships without login. Put it behind your company SSO, for example an identity-aware proxy, Azure AD / Entra App Proxy, or oauth2-proxy, or add JWT validation in `app/api/routes.py`. Don't expose it publicly without this.
- [ ] **HTTPS.** Terminate TLS at your load balancer or ingress.
- [ ] **CORS.** Set `CORS_ORIGINS` to your real frontend origin.
- [ ] **Multiple replicas.** Set `RATE_LIMIT_STORAGE_URI=redis://...` so rate limits are shared.
- [ ] **Secrets.** Inject `GEMINI_API_KEY` from your secret manager (Key Vault, Secret Manager, Kubernetes Secret). Never put it in an image or commit it.
- [ ] **Policies.** Replace the **sample** documents in `Backend/data/policies/` with your official ones.
- [ ] **Monitoring.** Ship the JSON logs to your log platform and alert on `level=ERROR` and on `/readyz` reporting `degraded`.
- [ ] **Gemini quota.** Check your project's rate limits in AI Studio and set a billing budget.

---

## 4. HR insights: what employees ask that the policies don't answer

```powershell
curl -H "X-Admin-Token: <ADMIN_API_TOKEN from Backend/.env>" "http://localhost:8000/api/v1/admin/insights?days=30"
```

The response lists:
- **Unanswered questions**, grouped by question and ranked by how often they're asked. These are the clearest signal of which policy to write or clarify next.
- **Feedback** counts and the **not-helpful answers**, with the employees' comments.

The endpoint is disabled when `ADMIN_API_TOKEN` is unset. The data is stored in `Backend/data/insights.db`, or in the `backend-state` volume under Docker.

## 5. Adding your HR policies

**From the UI (recommended):** open **📄 Documents**, enter the `ADMIN_API_TOKEN` from `Backend/.env`, then drag in PDF, Word (.docx), Markdown or text files. Uploading a file with an existing name replaces that document. Each upload is:
- limited to 20 files and `MAX_UPLOAD_MB` (default 10 MB) per file;
- rejected if it contains sensitive personal data, such as ID or card numbers or medical records;
- rejected if it has no extractable text, such as a scanned PDF. Run OCR on scanned PDFs first.

**From the file system:** copy files into `Backend/data/policies/`. They're picked up automatically on the next question, with no restart needed.

How documents are split into sections for citations:
- **Markdown:** `#` is the title; `##` and `###` headings are sections.
- **Word:** the Title and Heading 1–2 styles become sections.
- **PDF:** one section per page, cited as "Page N".

## 6. Configuration reference

All settings are environment variables. See [Backend/.env.example](Backend/.env.example).

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | *(required)* | Gemini API key from AI Studio |
| `GEMINI_MODEL` | `gemini-3.5-flash` | Chat model. Check the [current models list](https://ai.google.dev/gemini-api/docs/models) and switch to a newer one if available. |
| `GEMINI_FALLBACK_MODELS` | `gemini-3.5-flash-lite,gemini-3.1-flash-lite` | Tried in order when a model's quota runs out (each model has its own quota). Exhausted models are skipped until their quota resets. |
| `GEMINI_EMBEDDING_MODEL` | `gemini-embedding-001` | Embedding model for retrieval |
| `USE_OS_TRUST_STORE` | `true` | Use OS certificates for TLS. Required behind corporate TLS-inspection proxies. |
| `COMPANY_NAME`, `HR_CONTACT_EMAIL` | sample values | Used in the system prompt and fallback messages |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated allowed origins |
| `RATE_LIMIT` | `20/minute` | Per-IP limit on chat endpoints |
| `RATE_LIMIT_STORAGE_URI` | `memory://` | Use `redis://host:6379/0` when running several replicas |
| `MAX_MESSAGE_CHARS` / `MAX_HISTORY_MESSAGES` | `4000` / `20` | Request size limits |
| `RETRIEVAL_TOP_K` / `RETRIEVAL_MIN_SCORE` | `4` / `0.62` | Retrieval tuning. Unrelated policy chunks still score about 0.58–0.62 with `gemini-embedding-001`. |
| `KNOWLEDGE_GAP_SCORE` | `0.66` | Below this best-match score, a question is logged as not covered by the policies |
| `ANSWER_CACHE_SIZE` / `ANSWER_CACHE_TTL_SECONDS` / `ANSWER_CACHE_SIMILARITY` | `1000` / `86400` / `0.90` | Answer cache. Set the size to `0` to disable it. |
| `ATTACHMENTS_ENABLED` / `ATTACHMENT_TTL_MINUTES` / `MAX_ATTACHMENT_MB` / `MAX_ATTACHMENTS_PER_MESSAGE` | `true` / `60` / `10` / `5` | Employee chat attachments |
| `MAX_HISTORY_CHARS` | `12000` | Total conversation history sent to Gemini |
| `INSIGHTS_ENABLED` / `INSIGHTS_RETENTION_DAYS` / `ADMIN_API_TOKEN` | `true` / `90` / unset | HR insights storage and admin access |
| `ENVIRONMENT` | `development` | `production` disables `/docs` |
| `LOG_LEVEL` | `OFF` | `OFF` shows only uvicorn's default terminal output. Set `INFO` (or `DEBUG`/`WARNING`/`ERROR`) for structured JSON app logs with request IDs, recommended in production. |

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/chat/stream` | `{message, history}` → SSE events: `sources`, `token`…, then `done` or `error` |
| `POST` | `/api/v1/chat` | Same input, returns a single JSON response `{answer, sources, blocked}` |
| `POST` | `/api/v1/feedback` | `{rating: 1 or -1, question, answer, sources, comment?}` → 204 |
| `GET` | `/api/v1/config` | Company name, HR contact email, whether feedback is enabled |
| `GET` | `/api/v1/admin/insights` | HR insights. Requires the `X-Admin-Token` header. |
| `GET` | `/api/v1/documents` | List policy documents (public) |
| `GET` | `/api/v1/documents/{name}` | Open or download a document (public) |
| `POST` | `/api/v1/admin/documents` | Upload files as multipart `files`. Requires `X-Admin-Token`. |
| `DELETE` | `/api/v1/admin/documents/{name}` | Delete a document. Requires `X-Admin-Token`. |
| `POST` | `/api/v1/attachments` | Attach a file to a conversation (multipart `file`). Returns `{id, name, kind}`; pass `attachment_ids` to the chat endpoints. |
| `GET` | `/healthz` | Liveness |
| `GET` | `/readyz` | Readiness: Gemini reachability, index size, retrieval mode |

## Troubleshooting

- **`/readyz` shows `retrieval_mode: keyword` and chat says "invalid Gemini API key".** `GEMINI_API_KEY` is missing or wrong in `Backend/.env`.
- **`CERTIFICATE_VERIFY_FAILED` in the logs.** A corporate proxy is intercepting TLS. Keep `USE_OS_TRUST_STORE=true`. Inside Docker, add your corporate root CA to the backend image.
- **"Too many requests".** You hit either the app's `RATE_LIMIT` or your Gemini quota. On the **free tier each model allows only about 5 requests per minute and 20 per day** (verified on this key). The fallback models stretch this a little, but it's only enough for testing. Enable billing in AI Studio for real usage. Look for `Gemini quota exhausted for model` in the backend log.
- **Port 8000 already in use.** Run the backend on another port with `uvicorn ... --port 8001` and update the proxy target in `Frontend/vite.config.ts`.
