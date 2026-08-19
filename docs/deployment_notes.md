# Deployment Notes for Vocal Commerce Sales Agent

This repository is configured for automatic deployment on Render using Blueprints.

## Services Architecture

- **PostgreSQL Database**: Configured via Render's managed PostgreSQL.
- **Upstash/Render Redis**: For caching, state preservation, and rate-limiting.
- **FastAPI Backend Services**: Configured with Docker runtime to support Pipecat and LangGraph environment dependencies.
- **Next.js Static/Node Frontend**: Serves the broadcast-console user dashboard.
