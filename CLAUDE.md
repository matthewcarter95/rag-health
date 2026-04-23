# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RAG Health is a full-stack AI-powered gut health assistant that combines:
- LLM-powered RAG using AWS Bedrock (Claude Sonnet)
- Fine-grained access control via Auth0 FGA (ABAC model)
- Subscription tier-based content filtering (basic, premium, clinical, research)
- Google Calendar integration via Auth0 Connected Accounts API
- React 19 frontend with Auth0 authentication

## Project Structure

```
rag-health/
├── frontend/rag-health-ui/     # React 19 + TypeScript chat UI
├── backend/lambda/rag-agent/   # Python Lambda (handler, RAG chain, FGA retriever)
├── content/data/               # JSON content documents (microbiome, probiotics, etc.)
├── content/vectorstore/        # FAISS index (generated)
├── infrastructure/sam/         # SAM CloudFormation template
├── infrastructure/fga/         # Auth0 FGA model and tuples
├── scripts/                    # Deployment and build scripts
└── agent/prompts/              # System prompts
```

## Build and Run Commands

### Frontend (frontend/rag-health-ui/)
```bash
npm start       # Dev server on localhost:3000
npm run build   # Production build
npm test        # Run Jest tests
```

### Backend Deployment
```bash
./scripts/deploy.sh [dev|staging|prod]  # Full SAM deployment
python scripts/build-vectorstore.py     # Rebuild FAISS index after content changes
./scripts/seed-fga-tuples.sh            # Seed FGA authorization tuples
./scripts/test-api.sh                   # Test API endpoints
```

**Prerequisites:** AWS CLI, SAM CLI, Docker running

## Architecture

### API Endpoints (Lambda Function URL)
- `POST /query` - RAG query (requires `read:content` scope)
- `POST /chat` - Conversational interface with RAG + calendar
- `GET /calendar` - List events (requires `read:calendar` scope)
- `POST /calendar/create` - Create event (requires `write:calendar` scope)
- `GET /health` - Health check (no auth)

### Authentication Flow
1. Frontend: Auth0 redirect login → access token + MyAccount token
2. Backend: JWT validation against Auth0 JWKS endpoint
3. User context extraction (user_id, subscription_tier, roles)

### Authorization (FGA)
- Content filtered at query time based on user's subscription tier and roles
- FGA objects: `content:<content_id>`
- FGA subjects: `user:<user_id>`, `subscription_tier:<tier>#subscriber`, `role:<role>#member`
- Falls back to local tier-based filtering if FGA not configured

### RAG Pipeline
1. JSON documents in `content/data/` → Amazon Titan embeddings → FAISS index
2. Query: Vectorization → FAISS similarity search → FGA filter → Claude generation

### Key Backend Files
- `handler.py` - Lambda entry point, request routing
- `chains.py` - LangChain RAG chain configuration
- `fga_retriever.py` - FGA-filtered FAISS retriever
- `auth0_jwt.py` - JWT validation
- `google_calendar.py` - Calendar operations via Connected Accounts API

## Environment Variables

Key variables (see `.env.example` and `infrastructure/sam/template.yaml`):
- `AUTH0_DOMAIN`, `AUTH0_API_AUDIENCE`, `AUTH0_JWKS_URL` - Auth0 config
- `FGA_STORE_ID`, `FGA_API_URL`, `FGA_MODEL_ID` - FGA config (optional)
- `BEDROCK_MODEL_ID` - Claude model (default: `us.anthropic.claude-sonnet-4-5-20250929-v1:0`)
- `S3_CONTENT_BUCKET` - Vectorstore storage
- `SUBSCRIPTIONS_TABLE_NAME` - DynamoDB user subscriptions

## Content Schema

Content documents in `content/data/*.json` must follow `content/schemas/content-schema.json`:
- Required: `content_id`, `title`, `topic`, `tags`, `content`, `fga_object_id`
- Tags control access: `basic`, `patient-education`, `premium`, `clinical`, `research`
