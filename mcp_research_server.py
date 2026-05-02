"""
MCP Research Server — Company Research Agent.

3 tool categories for the assignment:
  1. INTERNET  -> search_company()        : Fetches company info from Wikipedia API
  2. FILE CRUD -> save/read/list/delete   : Manages research data as local JSON files
  3. PREFAB UI -> show_dashboard()        : Renders a live dashboard comparing companies

MCP (Model Context Protocol) works like USB for AI:
  - This SERVER advertises tools (like a USB device)
  - The agent CLIENT discovers and calls them via JSON-RPC over stdio
  - FastMCP handles all the protocol plumbing — we just write Python functions
"""

from __future__ import annotations
import json, os, re, subprocess, sys, time
from pathlib import Path
import requests
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("CompanyResearchServer")

DATA_DIR = Path(__file__).parent / "research_data"
DATA_DIR.mkdir(exist_ok=True)
GENERATED_APP = Path(__file__).parent / "generated_dashboard.py"

def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")

# ═══════════════════════════════════════════════════════════════════
# 1. INTERNET TOOL — Wikipedia API (free, no API key needed)
# ═══════════════════════════════════════════════════════════════════

@mcp.tool()
def search_company(company_name: str) -> str:
    """Search for a company on Wikipedia. Returns JSON with title, summary, extract, thumbnail, and URL."""
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{company_name}"
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "MCPResearchAgent/1.0"})
        if r.status_code == 404:
            r = requests.get(
                url.replace(company_name, company_name.replace(" ", "_")),
                timeout=10, headers={"User-Agent": "MCPResearchAgent/1.0"},
            )
        r.raise_for_status()
        d = r.json()
        return json.dumps({
            "title": d.get("title", company_name),
            "summary": d.get("description", "N/A"),
            "extract": d.get("extract", "N/A"),
            "thumbnail": d.get("thumbnail", {}).get("source", ""),
            "page_url": d.get("content_urls", {}).get("desktop", {}).get("page", ""),
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Search failed for '{company_name}': {e}"})

# ═══════════════════════════════════════════════════════════════════
# 2. FILE CRUD TOOLS — JSON files in research_data/
# ═══════════════════════════════════════════════════════════════════

@mcp.tool()
def save_research(company_name: str, data_json: str) -> str:
    """Save research data for a company to a local JSON file."""
    slug = _slug(company_name)
    fp = DATA_DIR / f"{slug}.json"
    try:
        parsed = json.loads(data_json)
    except json.JSONDecodeError:
        parsed = {"raw_data": data_json}
    parsed["_company_name"] = company_name
    parsed["_saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    fp.write_text(json.dumps(parsed, indent=2), encoding="utf-8")
    return f"Saved research for '{company_name}' to {fp.name}"

@mcp.tool()
def read_research(company_name: str) -> str:
    """Read saved research data for a company."""
    fp = DATA_DIR / f"{_slug(company_name)}.json"
    if not fp.exists():
        return json.dumps({"error": f"No research found for '{company_name}'"})
    return fp.read_text(encoding="utf-8")

@mcp.tool()
def list_research() -> str:
    """List all saved company research files."""
    files = sorted(DATA_DIR.glob("*.json"))
    items = []
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            items.append({"file": f.name, "company": d.get("_company_name", f.stem)})
        except Exception:
            items.append({"file": f.name, "company": f.stem})
    return json.dumps(items, indent=2)

@mcp.tool()
def delete_research(company_name: str) -> str:
    """Delete saved research data for a company."""
    fp = DATA_DIR / f"{_slug(company_name)}.json"
    if not fp.exists():
        return f"No research found for '{company_name}'"
    fp.unlink()
    return f"Deleted research for '{company_name}'"

# ═══════════════════════════════════════════════════════════════════
# 3. PREFAB UI TOOL — Generate & serve a dashboard
# ═══════════════════════════════════════════════════════════════════

@mcp.tool()
def show_dashboard() -> str:
    """Generate a Prefab dashboard from all saved research and serve it at http://127.0.0.1:5175."""
    files = sorted(DATA_DIR.glob("*.json"))
    if not files:
        return "No research data found! Use search_company and save_research first."

    companies = []
    for f in files:
        try:
            companies.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    if not companies:
        return "Could not parse any research files."

    source = _build_dashboard(companies)
    GENERATED_APP.write_text(source, encoding="utf-8")

    # Start prefab serve as a detached process so it survives MCP server shutdown
    try:
        # Free port 5175 from a previous run (best-effort, Windows)
        if sys.platform == "win32":
            os.system(
                'for /f "tokens=5" %a in (\'netstat -ano ^| findstr :5175 ^| findstr LISTENING\') '
                'do @taskkill /F /PID %a >nul 2>&1'
            )
        log = Path(__file__).parent / "prefab_server.log"
        lf = open(log, "w")
        popen_kwargs = dict(
            cwd=str(Path(__file__).parent),
            stdout=lf,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        if sys.platform == "win32":
            # Detach so the dashboard server survives our exit:
            #   DETACHED_PROCESS          - no shared console
            #   CREATE_NEW_PROCESS_GROUP  - own signal group
            #   CREATE_BREAKAWAY_FROM_JOB - escape kill-on-parent-exit Job Objects
            #                               (Windows Terminal / VS Code spawn us inside one).
            # Some Job Objects deny breakaway with Access Denied; fall back without it.
            base_flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            popen_kwargs["creationflags"] = base_flags | subprocess.CREATE_BREAKAWAY_FROM_JOB
        else:
            popen_kwargs["start_new_session"] = True
        # Pass a relative filename — Prefab's serve uses ':' as a path:attribute
        # separator, which collides with Windows drive letters like 'C:\...'.
        cmd = [sys.executable, "-X", "utf8", "-m", "prefab_ui.cli", "serve", GENERATED_APP.name]
        try:
            subprocess.Popen(cmd, **popen_kwargs)
        except OSError as e:
            if sys.platform == "win32" and getattr(e, "winerror", None) == 5:
                # Breakaway denied by parent Job Object — retry without it. The
                # dashboard will live as long as this MCP server does (i.e. as
                # long as agent.py keeps the stdio session open).
                popen_kwargs["creationflags"] = base_flags
                subprocess.Popen(cmd, **popen_kwargs)
            else:
                raise
        time.sleep(3)  # let the server bind the port before we report success
        return f"Dashboard with {len(companies)} companies at http://127.0.0.1:5175"
    except Exception as e:
        return f"Dashboard generated at {GENERATED_APP.name} but server failed: {e}"


def _build_dashboard(companies: list[dict]) -> str:
    """Generate Prefab Python source for the dashboard."""
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")[:200]

    tab_lines = []
    for comp in companies:
        name = esc(comp.get("_company_name", comp.get("title", "Unknown")))
        title = esc(comp.get("title", name))
        summary = esc(comp.get("summary", "N/A"))
        extract = esc(comp.get("extract", comp.get("raw_data", "No details")))
        slug = _slug(comp.get("_company_name", "co"))
        url = esc(comp.get("page_url", ""))

        tab_lines.append(f'                with Tab("{title}", value="{slug}"):')
        tab_lines.append( '                    with Column(gap=4):')
        tab_lines.append( '                        with Row(gap=4):')
        tab_lines.append( '                            with Column(gap=1):')
        tab_lines.append(f'                                Muted("Company")')
        tab_lines.append(f'                                H1("{title}")')
        tab_lines.append( '                            with Column(gap=1):')
        tab_lines.append(f'                                Muted("Description")')
        tab_lines.append(f'                                H3("{summary}")')
        tab_lines.append( '                        with Card():')
        tab_lines.append( '                            with CardContent():')
        tab_lines.append( '                                with Column(gap=2):')
        tab_lines.append(f'                                    H3("About")')
        tab_lines.append(f'                                    Text("{extract}")')
        tab_lines.append( '                        with Row(gap=2):')
        tab_lines.append(f'                            Badge("Researched", variant="success")')
        tab_lines.append(f'                            Badge("{summary}", variant="default")')
        if url:
            tab_lines.append(f'                        Muted("Source: {url}")')

    # Comparison tab
    tab_lines.append('                with Tab("Comparison", value="comparison"):')
    tab_lines.append('                    with Column(gap=4):')
    tab_lines.append('                        H2("All Companies at a Glance")')
    tab_lines.append('                        with Card():')
    tab_lines.append('                            with CardContent():')
    tab_lines.append('                                with Column(gap=3):')
    for comp in companies:
        n = esc(comp.get("_company_name", "Unknown"))
        s = esc(comp.get("summary", "N/A"))
        tab_lines.append(f'                                    with Row(gap=3):')
        tab_lines.append(f'                                        Text("{n}")')
        tab_lines.append(f'                                        Text("{s}")')
        tab_lines.append(f'                                        Badge("Saved", variant="success")')
    tab_lines.append(f'                        Muted("Total: {len(companies)} companies | Source: Wikipedia")')

    first_slug = _slug(companies[0].get("_company_name", "tab1"))
    header = '''"""Auto-generated Company Research Dashboard."""
from prefab_ui.app import PrefabApp
from prefab_ui.components import (
    Badge, Card, CardContent, CardHeader, CardTitle,
    Column, H1, H2, H3, Muted, Row, Tab, Tabs, Text,
)

with PrefabApp(css_class="max-w-5xl mx-auto p-6") as app:
    with Card():
        with CardHeader():
            CardTitle("Company Research Dashboard")
        with CardContent():
'''
    header += f'            with Tabs(value="{first_slug}"):\n'
    return header + "\n".join(tab_lines) + "\n"


if __name__ == "__main__":
    print(f"STARTING CompanyResearchServer -- data: {DATA_DIR}", file=sys.stderr)
    mcp.run(transport="stdio")
