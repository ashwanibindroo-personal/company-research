# Company Research Agent — MCP Assignment

An MCP-powered agentic system that researches companies from the internet, saves data locally, and displays results on a live Prefab dashboard.

## Demo

YouTube walkthrough: <https://youtu.be/BXiBItuI_yU>

## Architecture

```
User prompt  -->  Claude (Anthropic)  -->  MCP Server  -->  Results
                  (router)                 (3 tool types)
                                              |
                    +-------------------------+-------------------------+
                    |                         |                         |
              search_company             save_research            show_dashboard
              (Wikipedia API)            (JSON files)             (Prefab UI)
```

## The 3 Required Tool Categories

| # | Category | Tool(s) | What it does |
|---|----------|---------|-------------|
| 1 | Internet | `search_company` | Fetches company info from Wikipedia REST API |
| 2 | File CRUD | `save_research`, `read_research`, `list_research`, `delete_research` | Full CRUD on JSON files |
| 3 | Prefab UI | `show_dashboard` | Generates & serves a multi-tab dashboard |

## Setup

```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and add your Anthropic API key:

```
ANTHROPIC_API_KEY=sk-ant-api03-...
```

Get one at https://console.anthropic.com/settings/keys.

## Run

```bash
python agent.py
```

The agent will:
1. Search Wikipedia for TCS, Infosys, and Wipro
2. Save each company's data to `research_data/` as JSON
3. Generate and serve a Prefab dashboard at <http://127.0.0.1:5175>
4. Pause at a `Press Enter to stop...` prompt — open the URL in your browser, then press Enter to shut down

## Files

| File | Purpose |
|------|---------|
| `mcp_research_server.py` | MCP server with all 3 tool categories |
| `agent.py` | Claude-powered agentic loop |
| `requirements.txt` | Python dependencies |
| `.env.example` | Template for the Anthropic API key |
| `research_data/` | Auto-created directory for saved JSON files (git-ignored) |
| `generated_dashboard.py` | Auto-generated Prefab dashboard (git-ignored) |
