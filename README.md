# 🎙️ Calliope AI (VoiceSell) — Production-Grade Voice-Enabled Commerce Chatbot & RAG Recommendation System

<p align="center">
  <b>Ultra-low latency voice AI commerce engine powered by Pipecat, LangGraph, Qdrant, Deepgram, Cartesia, and FastAPI.</b>
</p>

---

## 🛑 The Problem

Modern e-commerce platforms and online storefronts face critical bottlenecks in customer support and conversion:
1. **High Support Overhead & Latency**: Traditional text chatbots are slow, impersonal, and unable to handle nuanced product inquiries in real-time.
2. **Hallucinated Product Knowledge**: LLMs frequently hallucinate product specifications, pricing, and stock availability when not tightly bound to a verified vector index.
3. **Complex Order Operations**: Allowing an AI agent to place, modify, or cancel orders carries high financial risk without robust confirmation gates and structured validation.
4. **Disjointed Voice Interfaces**: Building a conversational voice experience typically suffers from multi-second latency, lack of barge-in support, and poor audio streaming performance.

---

## 💡 How We Solve It

**Calliope AI** solves these challenges through an integrated, production-grade architecture:
- **Sub-500ms Voice Pipeline**: Combines WebRTC audio streaming, Voice Activity Detection (VAD), Deepgram STT, and Cartesia streaming TTS with barge-in capabilities.
- **RAG-Grounded Product Catalog**: Ingests and vectorises the Olist Brazilian E-Commerce dataset (74+ translated product categories, weights, dimensions, and synthetic pricing) into a high-performance **Qdrant** vector index with 768-dimension embeddings. Every answer is strictly cited and grounded.
- **LangGraph Conversational Agent**: Manages multi-turn stateful sales conversations, product recommendations, and secure order processing behind an explicit confirmation gate.
- **Single-Context Upsell Engine**: Automatically suggests exactly one highly relevant cross-sell or upsell item per confirmed transaction based on association mining and vector similarity.

---

## 🏗️ Architecture Diagrams

Architecture assets are maintained in the repository:
- **Voice Pipeline Architecture**: [`Images/VoiceArchitecture.png`](./Images/VoiceArchitecture.png)
- **Chat & RAG Architecture**: [`Images/ChatArchitecture.png`](./Images/ChatArchitecture.png)

```
[ User Voice / WebRTC ] 
       │
       ▼
[ Daily / Pipecat VAD ] 
       │
       ▼
[ Deepgram STT ] ──► [ LangGraph Agent ] ◄──► [ Qdrant Vector DB ]
                           │                             ▲
                           ▼                             │
                     [ FastAPI Backend ] ───────── [ Supabase / Postgres ]
                           │
                           ▼
[ Cartesia TTS ] ──► [ Streaming Audio Output ]
```

---

## 🛠️ Tech Stack & Key Components

- **Backend**: Python 3.11+, FastAPI, SQLAlchemy, Pydantic v2, UV package manager.
- **Frontend**: Next.js 15 (App Router), React, Tailwind CSS, Motion, Phosphor Icons.
- **Databases**: PostgreSQL (Supabase schema with UUIDs, JSONB, RLS) & Qdrant Vector Database.
- **AI & Voice Services**: LangGraph (agent state machine), LiteLLM (multi-provider failover), Deepgram (STT), Cartesia (TTS).
- **Quality & CI**: Ruff (linter/formatter), Pyright (strict static typing), Pytest (comprehensive unit & integration tests), GitHub Actions CI.

---

## 📁 Repository Structure

```
├── backend/                   # FastAPI Backend Application
│   ├── app/
│   │   ├── api/               # API endpoints & routing
│   │   ├── core/              # Configuration & logging
│   │   ├── db/                # Models & database session lifecycle
│   │   ├── schemas/           # Pydantic validation schemas
│   │   ├── services/          # Qdrant, RAG, LLM, and agent services
│   │   └── voice/             # Pipecat & Daily voice session pipeline
│   ├── scripts/               # Data ingestion & evaluation scripts
│   ├── tests/                 # Comprehensive test suite (pytest)
│   ├── Dockerfile             # Multi-stage production container
│   └── pyproject.toml         # UV dependency & project configuration
├── frontend/                  # Next.js 15 UI Application
│   ├── app/                   # App Router pages (Landing, Chat, Catalog, Orders, Admin)
│   ├── lib/                   # API clients and formatters
│   └── package.json           # Node dependencies
├── Images/                    # Architecture diagrams (Voice & Chat)
├── docs/                      # Architectural Decision Records (ADRs) & notes
├── render.yaml                # Render Infrastructure Blueprint
└── docker-compose.yml         # Local development orchestration
```

---

## 🚀 Getting Started & Local Development

### 1. Prerequisites
- Docker Desktop & Docker Compose
- Python 3.11+ with `uv` installed
- Node.js 18+ (for frontend)

### 2. Stand Up Infrastructure
```bash
docker compose up -d postgres qdrant
```

### 3. Run Backend Ingestion & Server
```bash
cd backend
cp .env.example .env
uv run python -m scripts.ingest_olist
uv run uvicorn app.main:app --reload --port 8000
```

### 4. Run Frontend Development Server
```bash
cd frontend
npm install
npm run dev
```
Open [http://localhost:3000](http://localhost:3000) for the UI dashboard and voice chat interface.

---

## 🧪 Testing & Verification

```bash
# Backend tests & type checking
cd backend
uv run pytest
uv run pyright app/
uv run ruff check app/ tests/
```
