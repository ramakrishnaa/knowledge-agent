# Knowledge Agent — ask questions about your own data

Feed it documents (upload files or paste text), then ask questions about them. A
LangGraph supervisor hands each question to the right agent: a **search agent** that finds
specific facts with semantic search over ChromaDB, and a **document analyst** that reads
whole documents to summarise, outline and compare them. Every answer cites the documents it
used, and every turn can be traced to **Arize Phoenix** or **Arize AX**. The backend runs
against any free OpenAI-compatible LLM (default: Groq's free tier with
`openai/gpt-oss-120b`); the Angular UI streams answers token by token and shows each
hand-off and tool call.

```
backend/
  app/agent.py      LangGraph graph: supervisor + search agent + document analyst
  app/rag.py        ChromaDB knowledge base and the agents' tools
  app/tools.py      tool registry
  app/telemetry.py  OpenInference tracing to Arize Phoenix / Arize AX
  app/main.py       FastAPI endpoints
  knowledge/        sample document loaded on first start
  Dockerfile        backend image (FastAPI + uvicorn, embedding model baked in)
frontend/           Angular 19 chat UI (standalone components + signals)
  Dockerfile        frontend image for local Docker runs (nginx, proxies /api)
docker-compose.yml  run both images locally
infra/terraform/    AWS: S3 + CloudFront (frontend), ECR + EC2 (backend)
scripts/            deploy-backend / deploy-frontend to AWS
```

## How it works

```
START → supervisor ─┬→ researcher (search_knowledge_base, list_knowledge_base) ─┐
          ↑         └→ analyst    (list_knowledge_base, read_document)        ─┤
          └─────────────────────────────────────────────────────────────────────┘   supervisor → FINISH → END
```

1. **Feed data.** Upload `.txt`, `.md`, `.pdf`, `.csv` or `.json` files (several at once,
   up to 10 MB each) or paste text with a title. Each document is split into ~1,000-character
   chunks with 150 characters of overlap, embedded locally with Chroma's `all-MiniLM-L6-v2`
   model (CPU, no API key) and stored in `backend/data/chroma`. Re-adding a document with the
   same name replaces it.
2. **Ask.** The supervisor reads the conversation and calls a `route` function to pick an
   agent. After each agent replies, control returns to the supervisor, which either sends the
   still-unanswered part to the other agent or finishes. "Summarise the travel policy and tell
   me the Tokyo hotel limit" goes to the analyst, then the search agent.
3. Each agent runs a streaming tool-calling loop (capped at `AGENT_MAX_STEPS` tool
   round-trips); hand-offs per message are capped at `AGENT_MAX_HANDOFFS`.
4. Each step goes to the UI as an event: `handoff`, `text`, `thinking`, `tool_start`,
   `tool_call`, `tool_result`, `notice`, `error`, `done`.

Conversation history is kept per session by a LangGraph checkpointer in server memory, so
restarting the server clears it. Documents persist in ChromaDB.

## Agents and tools

| Agent | Tools | Handles |
|---|---|---|
| Search agent | `search_knowledge_base`, `list_knowledge_base` | Specific facts, figures, rules and definitions, with `[file]` citations |
| Document analyst | `list_knowledge_base`, `read_document` | Summaries, outlines, explanations and comparisons of whole documents |

| Tool | What it does |
|---|---|
| `search_knowledge_base` | Semantic search; returns the best passages with source and similarity score |
| `list_knowledge_base` | Lists the documents and their chunk counts |
| `read_document` | Reads a document in order, paging through long ones |

To add an agent, add an entry to `AGENTS` in `backend/app/agent.py` with a title, a
description (the supervisor routes on it), its tool names and a prompt. New tools are
registered with the `@tool` decorator (see `backend/app/rag.py`).

`backend/knowledge/travel-policy.md`, a short fictional policy, is loaded the first time
the knowledge base is empty so there is something to ask about. Delete it from the sidebar
when you add your own data.

## Tracing with Arize

With tracing on, each question is one trace: the LangGraph run, a span per agent, every LLM
call (prompt, completion, token counts) and every retrieval with the passages and scores it
returned. Set **one** of these in `backend/.env` and restart the backend:

**Arize Phoenix, local (free, open source).** Install it in its own virtual environment:

```powershell
cd backend
python -m venv .venv-phoenix
.venv-phoenix\Scripts\pip install arize-phoenix
.venv-phoenix\Scripts\phoenix serve       # UI at http://localhost:6006
```

```
PHOENIX_COLLECTOR_ENDPOINT=http://localhost:6006
```

Phoenix keeps its data in `~/.phoenix`; set `PHOENIX_WORKING_DIR` to put it elsewhere.

**Phoenix Cloud.** Set `PHOENIX_COLLECTOR_ENDPOINT` to your space URL and `PHOENIX_API_KEY`.

**Arize AX (cloud).** Set `ARIZE_SPACE_ID` and `ARIZE_API_KEY` (Settings in https://app.arize.com).

Traces land in the project `knowledge-agent` (change with `TRACING_PROJECT_NAME`).

## Free LLM options

| Provider | Cost | Key | Notes |
|---|---|---|---|
| **Groq** (default) | Free tier | https://console.groq.com/keys | Very fast; good tool calling |
| OpenRouter | Free `:free` models | https://openrouter.ai/keys | Many models; lower rate limits |
| Google Gemini | Free tier | https://aistudio.google.com/apikey | OpenAI-compatible endpoint |
| Ollama | Free, runs locally | none | Needs a model download; slow on CPU-only machines |

Switch providers by editing `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` in
`backend/.env`; ready-made blocks are in `.env.example`. The model must support tool
calling and forced tool choice.

## Setup

Prerequisites: Python 3.10+, Node 18.19+ or 20.11+, and a free [Groq API key](https://console.groq.com/keys).

**Backend** (terminal 1):

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env          # then put your LLM_API_KEY in .env
uvicorn app.main:app --reload --port 8000
```

**Frontend** (terminal 2):

```powershell
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. The dev server forwards `/api` to the backend on port 8000.

## Configuration (`backend/.env`)

| Variable | Default | Notes |
|---|---|---|
| `LLM_BASE_URL` | `https://api.groq.com/openai/v1` | Any OpenAI-compatible endpoint |
| `LLM_API_KEY` | — | Required except for Ollama |
| `LLM_MODEL` | `openai/gpt-oss-120b` | Must support tool/function calling |
| `AGENT_MAX_STEPS` | `10` | Maximum tool round-trips per agent |
| `AGENT_MAX_HANDOFFS` | `4` | Maximum agent hand-offs per message |
| `PHOENIX_COLLECTOR_ENDPOINT` | — | Send traces to Phoenix |
| `PHOENIX_API_KEY` | — | Phoenix Cloud only |
| `ARIZE_SPACE_ID` / `ARIZE_API_KEY` | — | Send traces to Arize AX instead |
| `TRACING_PROJECT_NAME` | `knowledge-agent` | Project name in Phoenix / Arize |
| `DATA_DIR` | `backend/data` (`/data` in Docker) | Where ChromaDB stores documents |
| `EMBEDDING_MODEL_DIR` | `$DATA_DIR/onnx_models` (`/opt/models` in Docker) | Embedding model location |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Comma-separated origins allowed to call the API directly |

## Run with Docker

The frontend and backend build into two independent images:

```powershell
copy backend\.env.example backend\.env   # then set LLM_API_KEY
docker compose up --build                 # UI at http://localhost:8080
```

The backend image listens on 8000 (health check `GET /api/health`) and keeps its data in
the `/data` volume. The frontend image serves the build with nginx on port 80 and proxies
`/api` to `BACKEND_URL` (default `http://backend:8000`).

## Deploy to AWS (Terraform)

```
Browser ──► CloudFront (https://xxxx.cloudfront.net)
              ├─ /api/*  ──► EC2 t3.small, backend container from ECR (port 8000)
              └─ /*      ──► private S3 bucket (Angular build)
```

One HTTPS address serves both, so the UI needs no API URL or CORS settings. The backend
accepts traffic only from CloudFront. You reach the instance through SSM Session Manager,
so no SSH key or open port is needed. Expect about US$20/month, mostly the EC2 instance,
its disk and its public IP. S3 and CloudFront stay within the free tier at low traffic.

`infra/terraform` creates:
- the ECR repository `knowledge-agent-backend`;
- the EC2 instance with its IAM role, security group and Elastic IP;
- an SSM SecureString holding the backend `.env`;
- the S3 bucket;
- the CloudFront distribution.

Prerequisites: [Terraform](https://developer.hashicorp.com/terraform/install) 1.6+, the
[AWS CLI](https://aws.amazon.com/cli/) (`aws configure`), Docker running, Node.

```powershell
cd infra\terraform
copy terraform.tfvars.example terraform.tfvars   # set region and llm_api_key
terraform init
terraform apply
cd ..\..
.\scripts\deploy-backend.ps1    # build, push to ECR, restart the container
.\scripts\deploy-frontend.ps1   # build, upload to S3, refresh CloudFront
```

On macOS/Linux use `scripts/deploy-backend.sh` and `scripts/deploy-frontend.sh`. Open the
`app_url` output. The first CloudFront deployment takes a few minutes. On first boot the
instance installs Docker and keeps retrying until the backend image is in ECR. If
`deploy-backend` runs before that boot finishes, its restart step fails; run it again.

- **Updating the app:** rerun the matching deploy script.
- **Changing LLM settings:** edit `terraform.tfvars`, run `terraform apply`, then
  `deploy-backend` to restart the container with the new `.env`.
- **Logs:** run `aws ssm start-session --target <backend_instance_id>`, then
  `sudo docker logs -f knowledge-agent-backend`.
- **Removing everything:** `terraform destroy`. This also deletes the uploaded documents.

Things to know:
- Documents and the embedding store live on the instance's disk under
  `/opt/knowledge-agent/data`. They survive restarts and redeploys, but not replacing the
  instance. Terraform ignores newer AMIs and start-up script changes so that it doesn't
  replace the instance by accident.
- Conversation history is in memory and is cleared when the container restarts.
- `llm_api_key` is stored in the Terraform state file, so keep `terraform.tfstate` private.
  For a team, use an encrypted S3 backend.
- CloudFront waits up to 60 s for the first byte and between streamed chunks. A step where
  the LLM is silent for longer is cut off.

## API

| Method | Path | |
|---|---|---|
| `GET` | `/api/health` | Status, model and tracing backend |
| `GET` | `/api/agents` | Agents with their descriptions and tools |
| `GET` | `/api/tools` | All tool names and descriptions |
| `POST` | `/api/chat` | `{message, session_id?}` → SSE stream |
| `DELETE` | `/api/sessions/{id}` | Clear a conversation |
| `GET` | `/api/documents` | Documents and their chunk counts |
| `POST` | `/api/documents` | Multipart `file` upload (.txt, .md, .pdf, .csv, .json) |
| `POST` | `/api/documents/text` | `{title, text}` → add pasted text as a document |
| `DELETE` | `/api/documents/{source}` | Remove a document |

Interactive docs: http://localhost:8000/docs
