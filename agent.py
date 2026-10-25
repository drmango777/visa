"""The Sail-by agent (optional "Explain it simply" button).

With OPENAI_API_KEY set, an OpenAI model calls the tools below, reads the
real results, and explains them in plain words in the chosen language.
Without a key, or if the API fails, a fixed template explains the same numbers,
so the demo never breaks. The map and numbers on screen never come from the AI.
"""

import json
import os
from datetime import date

import tools

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
MAX_STEPS = 4

SYSTEM_PROMPT = """You help people who are coming home from prison in Miami-Dade County understand what changed while they were away and what is around where they will live.

Use the tools to get real numbers. Never invent prices, dates, routes, or rules.
Write at about a 6th-grade reading level. Short sentences. Warm and direct. No jargon.
Write in {language}.
Reply with 4 to 7 short sentences of plain text (no markdown, no lists): what matters most first (rent, then buses, then prices), then one practical next step.
Do not give legal advice. For anything about release conditions, voting rights, or records, say to check with their case manager or the official site."""

TOOL_SPECS = [
    {"type": "function", "function": {
        "name": "get_area_facts",
        "description": "Rent change, price changes, and nearby bus routes (with what changed on each) around a place.",
        "parameters": {"type": "object", "properties": {
            "lat": {"type": "number"}, "lon": {"type": "number"},
            "zip": {"type": "string"}, "radius_miles": {"type": "number"},
        }, "required": ["lat", "lon", "zip", "radius_miles"]}}},
    {"type": "function", "function": {
        "name": "get_changes_since",
        "description": "Everything that changed since a date: minimum wage, rent, prices, new rules and services.",
        "parameters": {"type": "object", "properties": {
            "since": {"type": "string", "description": "YYYY-MM-DD"},
            "area_id": {"type": "string", "description": "little-havana, downtown, or overtown"},
            "zip": {"type": "string"},
        }, "required": ["since", "area_id", "zip"]}}},
]


def _slim(result):
    """Trim big map data so the model only sees what it needs."""
    result = json.loads(json.dumps(result))
    buses = result.get("buses")
    if isinstance(buses, dict) and buses.get("available"):
        buses["routes"] = [{k: v for k, v in r.items() if k != "shapes"} for r in buses["routes"][:12]]
        buses.pop("stops", None)
    rent = result.get("rent")
    if isinstance(rent, dict):
        rent.pop("history", None)
    return result


def _call(name, args):
    if name == "get_area_facts":
        return tools.nearby(args["lat"], args["lon"], args.get("zip", ""), args.get("radius_miles", 1))
    if name == "get_changes_since":
        return tools.changes_since(args["since"], args.get("area_id"), args.get("zip", ""))
    raise ValueError(f"Unknown tool {name}")


def _ask(context):
    if context["kind"] == "area":
        return (f"Explain what is around this place. Call get_area_facts with lat={context['lat']}, "
                f"lon={context['lon']}, zip='{context.get('zip', '')}', radius_miles={context['radius_miles']}.")
    return (f"This person was convicted on {context['since']}. Explain what changed since then. Call "
            f"get_changes_since with since='{context['since']}', area_id='{context.get('area_id') or ''}', "
            f"zip='{context.get('zip', '')}'.")


def _run_ai(context, language):
    from openai import OpenAI
    client = OpenAI()
    messages = [{"role": "system", "content": SYSTEM_PROMPT.format(language=language)},
                {"role": "user", "content": _ask(context)}]
    for _ in range(MAX_STEPS):
        msg = client.chat.completions.create(model=MODEL, messages=messages, tools=TOOL_SPECS).choices[0].message
        if not msg.tool_calls:
            return (msg.content or "").strip()
        messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments}} for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            try:
                result = _slim(_call(tc.function.name, json.loads(tc.function.arguments or "{}")))
            except Exception as e:  # noqa: BLE001
                result = {"error": str(e)}
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)})
    raise RuntimeError("The agent took too many steps.")


def _money(n):
    return f"${n:,.0f}"


def _backup(context):
    """Template explanation from the same tools."""
    if context["kind"] == "area":
        f = tools.nearby(context["lat"], context["lon"], context.get("zip", ""), context["radius_miles"])
        parts = []
        r = f["rent"]
        if r.get("available") and r.get("then"):
            parts.append(f"Typical rent here is {_money(r['now'])} a month. In {r['then_year']} it was {_money(r['then'])}, "
                         f"so it is up {_money(r['diff'])} ({r['pct']}%).")
        b = f["buses"]
        if b.get("available"):
            n = len(b["routes"])
            parts.append(f"{n} bus or train route{'s' if n != 1 else ''} stop within {context['radius_miles']} mile"
                         f"{'s' if context['radius_miles'] != 1 else ''}." if n else "No bus stops are this close. Try a bigger circle.")
            changed = [x for x in b["routes"] if x.get("change")][:2]
            for x in changed:
                parts.append(f"Route {x['route']}: {x['change']}")
        parts.append("Check the GO Miami-Dade Transit app or call 311 before you count on a bus.")
        return " ".join(parts)
    f = tools.changes_since(context["since"], context.get("area_id"), context.get("zip", ""))
    parts = [f"It has been about {f['years_away']} years since you were convicted."]
    w = f["min_wage"]
    if w["now"]:
        parts.append(f"Florida's minimum wage is now {_money(w['now'])} an hour" +
                     (f", up from {_money(w['then'])}." if w["then"] else "."))
    r = f["rent"]
    if r.get("available") and r.get("then"):
        parts.append(f"Rent in {r['zip']} is up {_money(r['diff'])} a month since {r['then_year']}.")
    p = next((i for i in f["prices"].get("items", []) if i["id"] == "cpi_miami"), None)
    if p:
        parts.append(f"Prices overall in Miami are up {p['pct']}%.")
    news = [e for e in f["events"] if not e["still_true"]][:2]
    parts += [e["title"] + "." for e in news]
    return " ".join(parts)


def explain(context, language="English"):
    """Returns {"text", "mode"}. Falls back to the template if the AI is unavailable."""
    if os.getenv("OPENAI_API_KEY"):
        try:
            return {"text": _run_ai(context, language), "mode": "agent"}
        except Exception as e:  # noqa: BLE001
            note = f"(AI unavailable: {type(e).__name__}. Showing the basic summary.) "
            text = _backup(context)
            if language != "English":
                note += f"(Basic summary is English only.) "
            return {"text": note + text, "mode": "backup"}
    text = _backup(context)
    if language != "English":
        text = f"(Add an OpenAI key to get this in {language}.) " + text
    return {"text": text, "mode": "backup"}
