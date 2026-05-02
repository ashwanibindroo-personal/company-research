"""
Agentic Loop — Company Research Agent (Anthropic edition).

This is the "brain" that connects to the MCP server and uses the Anthropic
Claude API to decide which tools to call and in what order.

HOW THE AGENTIC LOOP WORKS:
  1. Connect to MCP server (mcp_research_server.py) via stdio
  2. Discover all available tools automatically
  3. Send Claude a prompt describing the task + available tools
  4. Claude responds with FUNCTION_CALL: tool_name|arg1|arg2
  5. We execute the tool via MCP and feed the result back
  6. Repeat until Claude says FINAL_ANSWER

The LLM is the "router" — it decides which tool to call next based on
the task and previous results. It never executes code directly; it only
picks tools from the menu.

Run:
    python agent.py
"""

import asyncio
import os

import anthropic
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

load_dotenv()

MODEL = "claude-opus-4-7"
MAX_ITERATIONS = 15
MAX_TOKENS = 1024  # tiny — we only need one directive line per turn

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def describe_tools(tools) -> str:
    lines = []
    for i, t in enumerate(tools, 1):
        props = (t.inputSchema or {}).get("properties", {})
        params = ", ".join(f"{n}: {p.get('type', '?')}" for n, p in props.items()) or "no params"
        lines.append(f"{i}. {t.name}({params}) -- {t.description or ''}")
    return "\n".join(lines)


def coerce(value: str, schema_type: str):
    if schema_type == "integer":
        return int(value)
    if schema_type == "number":
        return float(value)
    if schema_type == "boolean":
        return value.lower() in ("true", "1", "yes")
    return value


def call_llm(system_prompt: str, user_prompt: str) -> str:
    """Single Claude call — returns the assistant's text reply."""
    message = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return next((b.text for b in message.content if b.type == "text"), "").strip()


async def main():
    server_params = StdioServerParameters(
        command="python",
        args=["mcp_research_server.py"],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("Connected to CompanyResearchServer")

            tools = (await session.list_tools()).tools
            tools_desc = describe_tools(tools)
            print(f"Loaded {len(tools)} tools\n")

            system_prompt = f"""You are a company research agent with access to MCP tools.
You solve tasks by calling tools ONE AT A TIME and observing their results.

Available tools:
{tools_desc}

Respond with EXACTLY ONE line, in one of these two formats:
  FUNCTION_CALL: tool_name|arg1|arg2|...
  FINAL_ANSWER: <short summary of what you did>

Rules:
- Provide args in the exact order of the tool's parameters.
- Do not invent tools that are not listed above.
- After each FUNCTION_CALL you'll receive the result; use it to decide the next step.
- When saving research, pass the FULL JSON data from search_company as the data_json argument.
- When the task is complete, emit FINAL_ANSWER.
"""

            task = (
                "Research these companies: Tata Consultancy Services, Infosys, and Wipro. "
                "For each company: 1) Search for it using search_company, "
                "2) Save the search results using save_research. "
                "After all three are saved, call show_dashboard to display the results. "
                "Finally, give a FINAL_ANSWER summarizing what you did."
            )

            print(f"TASK: {task}\n")
            print("=" * 60)

            history: list[str] = []
            for iteration in range(1, MAX_ITERATIONS + 1):
                print(f"\n--- Iteration {iteration} ---")

                context = "\n".join(history) if history else "(no prior steps)"
                user_prompt = (
                    f"Task: {task}\n\n"
                    f"Previous steps:\n{context}\n\n"
                    f"What is your next single action?"
                )

                try:
                    raw = await asyncio.to_thread(call_llm, system_prompt, user_prompt)
                except anthropic.RateLimitError as e:
                    print(f"Rate limited (SDK retries exhausted): {e}")
                    break
                except anthropic.APIStatusError as e:
                    print(f"API error {e.status_code}: {e.message}")
                    break
                except anthropic.APIConnectionError as e:
                    print(f"Connection error: {e}")
                    break

                # Extract the directive line (skip any markdown/prose)
                text = raw
                for line in raw.splitlines():
                    s = line.strip().lstrip("`").lstrip()
                    if s.startswith("FUNCTION_CALL:") or s.startswith("FINAL_ANSWER:"):
                        text = s
                        break

                print(f"LLM: {text}")

                if text.startswith("FINAL_ANSWER:"):
                    print("\n=== Agent done ===")
                    print(text)
                    print("\nDashboard is live at http://127.0.0.1:5175")
                    try:
                        await asyncio.to_thread(
                            input, "Press Enter to stop the dashboard and exit..."
                        )
                    except EOFError:
                        # No interactive stdin (piped/background) — let the dashboard
                        # keep running in its own detached process and exit cleanly.
                        pass
                    break

                if not text.startswith("FUNCTION_CALL:"):
                    print("Unexpected response format -- retrying...")
                    history.append(f"Iteration {iteration}: (bad format, retrying)")
                    continue

                _, call = text.split(":", 1)
                parts = [p.strip() for p in call.split("|")]
                func_name, raw_args = parts[0], parts[1:]

                tool = next((t for t in tools if t.name == func_name), None)
                if tool is None:
                    msg = f"Unknown tool {func_name!r}"
                    print(msg)
                    history.append(f"Iteration {iteration}: {msg}")
                    continue

                props = (tool.inputSchema or {}).get("properties", {})
                arguments = {
                    name: coerce(val, info.get("type", "string"))
                    for (name, info), val in zip(props.items(), raw_args)
                }

                print(f"-> {func_name}({arguments})")
                try:
                    result = await session.call_tool(func_name, arguments=arguments)
                    payload = (
                        result.content[0].text
                        if result.content and hasattr(result.content[0], "text")
                        else str(result)
                    )
                except Exception as e:
                    payload = f"ERROR: {e}"

                # Truncate long payloads for display
                display = payload[:300] + "..." if len(payload) > 300 else payload
                print(f"<- {display}")
                history.append(
                    f"Iteration {iteration}: called {func_name}({arguments}) -> {payload}"
                )
            else:
                print("\nReached MAX_ITERATIONS without FINAL_ANSWER.")


if __name__ == "__main__":
    asyncio.run(main())
