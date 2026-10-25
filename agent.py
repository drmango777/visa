"""The Reentry Compass agent.

With OPENAI_API_KEY set, an OpenAI model decides which tools to call,
reads the results, and writes the plan and drafts (in the chosen language).

Without a key, or if the API call fails, it runs the same tools in a fixed
order and fills in templates. That way the live demo never dies on stage.
"""

import json
import os

import tools

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
MAX_STEPS = 6

SYSTEM_PROMPT = """You are Reentry Compass, a planning assistant for people in their first weeks after release from jail or prison in Miami-Dade County, and for the counselors who help them.

Your job: find where the person can realistically live given their budget, curfew, and required check-in, and catch conflicts between their obligations before they turn into violations.

How to work:
- Always call find_areas first.
- Then call get_help_nearby for the best 1 to 3 areas.
- Then call get_civic_alerts for the areas you plan to mention.

Rules:
- Base every fact on tool results. Never invent rents, times, places, rules, or laws.
- Some data is sample data. Say so when it matters.
- You do not give legal advice. Anything about release conditions must be confirmed with the person's officer or case manager.
- Never tell anyone how to vote or what position to take. Public comment drafts describe the person's own situation in plain, factual terms and leave a spot for them to add their view in their own words.
- Write at about a 6th-grade reading level. Warm, direct, no jargon.
- Write all text in {language}.

When you are done, reply with ONLY a JSON object (no markdown, no code fences) with these keys:
"summary": 2 to 4 sentences on the best options and the biggest risk.
"next_step": one concrete action for this week.
"drafts": a list of 1 to 3 objects, each with "to", "subject", "body". Good drafts: a note to the probation officer asking to adjust a check-in time with the transit times attached; a message to a shelter or landlord; a public comment about a civic alert."""

TOOL_SPECS = [
    {"type": "function", "function": {
        "name": "find_areas",
        "description": "Rank neighborhoods within a radius of the check-in site by whether transit gets the person home before curfew, whether rent fits the budget, and how much help is nearby.",
        "parameters": {"type": "object", "properties": {
            "checkin_site": {"type": "string", "description": "Check-in site id, e.g. 'doral'"},
            "radius_miles": {"type": "integer", "enum": [5, 10, 20]},
            "budget": {"type": "integer", "description": "Monthly housing budget in dollars"},
            "curfew": {"type": "string", "description": "24-hour time, e.g. '21:00'"},
            "obligation_end": {"type": "string", "description": "When the evening check-in or class ends, 24-hour time"},
        }, "required": ["checkin_site", "radius_miles", "budget", "curfew", "obligation_end"]}}},
    {"type": "function", "function": {
        "name": "get_help_nearby",
        "description": "List clinics, libraries, shelters, and housing programs in an area, including whether they accept people with records.",
        "parameters": {"type": "object", "properties": {
            "area_id": {"type": "string"},
            "needs": {"type": "array", "items": {"type": "string"}},
        }, "required": ["area_id"]}}},
    {"type": "function", "function": {
        "name": "get_civic_alerts",
        "description": "Upcoming county decisions (zoning votes, bus changes, shelter funding) that affect these areas, with who decides and how to comment.",
        "parameters": {"type": "object", "properties": {
            "area_ids": {"type": "array", "items": {"type": "string"}},
        }, "required": ["area_ids"]}}},
]


def _call_tool(name, args):
    if name == "find_areas":
        return tools.find_areas(**args)
    if name == "get_help_nearby":
        return tools.get_help_nearby(args["area_id"], args.get("needs"))
    if name == "get_civic_alerts":
        return tools.get_civic_alerts(args["area_ids"])
    raise ValueError(f"Unknown tool {name}")


def _trace_line(name, args, result):
    if name == "find_areas":
        good = sum(1 for a in result["areas"] if a["recommended"])
        return f"Checked {len(result['areas'])} areas within {args['radius_miles']} miles; {good} work with the curfew and budget."
    if name == "get_help_nearby":
        return f"Found {len(result['places'])} places near {tools.AREA_BY_ID[args['area_id']]['name']}."
    if name == "get_civic_alerts":
        n = len(result['alerts'])
        return f"Found {n} upcoming county decision{'' if n == 1 else 's'}."
    return "Done."


def _parse_json(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


# ---------- AI agent ----------

def _run_ai(profile):
    from openai import OpenAI
    client = OpenAI()

    user_msg = (
        f"Plan for {profile['first_name'] or 'this person'}.\n"
        f"Check-in site: {profile['checkin_site']}\n"
        f"Evening obligation ends at: {profile['obligation_end']}\n"
        f"Curfew: {profile['curfew']}\n"
        f"Monthly housing budget: ${profile['budget']}\n"
        f"Search radius: {profile['radius_miles']} miles\n"
        f"Also needs nearby: {', '.join(profile['needs']) or 'nothing specific'}"
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(language=profile["language"])},
        {"role": "user", "content": user_msg},
    ]
    trace = []

    for _ in range(MAX_STEPS):
        resp = client.chat.completions.create(model=MODEL, messages=messages, tools=TOOL_SPECS)
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return _parse_json(msg.content), trace

        messages.append({
            "role": "assistant", "content": msg.content or "",
            "tool_calls": [{"id": tc.id, "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                           for tc in msg.tool_calls],
        })
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments or "{}")
            try:
                result = _call_tool(tc.function.name, args)
                trace.append({"tool": tc.function.name, "note": _trace_line(tc.function.name, args, result)})
            except Exception as e:
                result = {"error": str(e)}
                trace.append({"tool": tc.function.name, "note": f"Error: {e}"})
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)})

    raise RuntimeError("The agent took too many steps without finishing.")


# ---------- backup mode (no API) ----------

def _run_backup(profile, ranked, help_by_area, alerts):
    name = profile["first_name"] or "you"
    good = [a for a in ranked["areas"] if a["recommended"]]
    blocked = [a for a in ranked["areas"] if a["trip"]["status"] in ("conflict", "no_service")
               and a["room_fit"] != "over"]

    if good:
        names = " and ".join(a["name"] for a in good[:2])
        summary = (f"{names} {'are' if len(good[:2]) > 1 else 'is'} the best fit: transit gets {name} home "
                   f"before the {good[0]['trip']['curfew']} curfew, and room rentals are close to the "
                   f"${profile['budget']} budget.")
    else:
        summary = "No area in this radius works with both the curfew and the budget. Try a bigger radius or ask about moving the check-in time."
    if blocked:
        b = blocked[0]
        if good and b["room_rent"] < good[0]["room_rent"]:
            summary += f" {b['name']} looks cheaper, but it's a trap: {b['trip']['message'][0].lower()}{b['trip']['message'][1:]}"
        else:
            summary += f" {b['name']} fits the budget, but {b['trip']['message'][0].lower()}{b['trip']['message'][1:]}"
    rising = [a for a in good if a["rising_fast"]]
    if rising:
        summary += f" Rent in {rising[0]['name']} is rising fast ({rising[0]['rent_change_pct_2yr']}% in 2 years)."

    if good:
        top = good[0]
        places = help_by_area.get(top["area_id"], [])
        record_ok = next((p for p in places if p.get("accepts_records")), None)
        next_step = (f"This week, contact the {record_ok['name'].lower()} in {top['name']}."
                     if record_ok else f"This week, take a test ride from your check-in to {top['name']} to make sure you beat your {top['trip']['curfew']} curfew.")
    else:
        next_step = "Ask your officer or case manager about moving your check-in earlier."

    drafts = []
    pinch = (next((a for a in ranked["areas"] if a["trip"]["status"] == "conflict" and a["room_fit"] != "over"), None)
             or next((a for a in ranked["areas"] if a["trip"]["status"] in ("tight", "conflict")), None))
    if pinch:
        t = pinch["trip"]
        drafts.append({
            "to": "Probation officer",
            "subject": "Request to move my evening check-in earlier",
            "body": (f"Hello,\n\nI'm looking for housing I can afford while keeping my curfew. My check-in ends at "
                     f"{t['obligation_end']}, and the ride to {pinch['name']} takes {t['ride_minutes']} minutes "
                     f"({t['mode']}). That gets me home at {t['arrive'] or 'no time, because the last bus is gone'}, "
                     f"but my curfew is {t['curfew']}\n\nWould it be possible to move my check-in earlier so I can make it home "
                     f"on time? I want to follow every condition.\n\nThank you,\n{profile['first_name'] or '[Your name]'}"),
        })
    if good:
        top = good[0]
        drafts.append({
            "to": f"Housing contact in {top['name']}",
            "subject": "Looking for a room",
            "body": (f"Hi,\n\nI'm looking for a room in {top['name']} for about ${profile['budget']} a month. "
                     f"I recently came home and I'm working on getting settled. I have a {top['trip']['curfew']} curfew, "
                     f"so I'd need a place close to the bus.\n\nDo you have anything available? I'm happy to answer any "
                     f"questions.\n\nThank you,\n{profile['first_name'] or '[Your name]'}"),
        })
    if alerts:
        a = alerts[0]
        drafts.append({
            "to": a["decided_by"],
            "subject": f"Public comment: {a['title']}",
            "body": (f"My name is {profile['first_name'] or '[Your name]'}. I live in or near {a['area_name']} and I depend "
                     f"on this area for affordable housing and transit.\n\n{a['what']}\n\n[Add in your own words how this "
                     f"decision would affect you.]\n\nThank you for considering residents like me."),
        })

    if profile["language"] != "English":
        summary = f"(Backup mode writes in English only. Add an OpenAI key for {profile['language']}.) " + summary

    trace = [
        {"tool": "find_areas", "note": f"Checked {len(ranked['areas'])} areas within {ranked['radius_miles']} miles; {len(good)} work with the curfew and budget."},
        {"tool": "get_help_nearby", "note": f"Looked up help near the top {min(3, len(ranked['areas']))} areas."},
        {"tool": "get_civic_alerts", "note": f"Found {len(alerts)} upcoming county decision{'' if len(alerts) == 1 else 's'}."},
    ]
    return {"summary": summary, "next_step": next_step, "drafts": drafts}, trace


# ---------- entry point ----------

def run(profile):
    # The cards in the UI always come from the tools directly, so the
    # numbers on screen are exact even if the AI words things differently.
    ranked = tools.find_areas(profile["checkin_site"], profile["radius_miles"], profile["budget"],
                              profile["curfew"], profile["obligation_end"])
    top_ids = [a["area_id"] for a in ranked["areas"][:3]]
    help_by_area = {aid: tools.get_help_nearby(aid, profile["needs"])["places"] for aid in top_ids}
    alerts = tools.get_civic_alerts([a["area_id"] for a in ranked["areas"]])["alerts"]

    mode, error = "agent", None
    if os.getenv("OPENAI_API_KEY"):
        try:
            plan, trace = _run_ai(profile)
        except Exception as e:
            error = f"AI agent unavailable ({type(e).__name__}); used backup mode."
            plan, trace = _run_backup(profile, ranked, help_by_area, alerts)
            mode = "backup"
    else:
        plan, trace = _run_backup(profile, ranked, help_by_area, alerts)
        mode = "backup"

    logged = tools.log_conflicts(profile["checkin_site"], ranked)
    trace.append({"tool": "log_conflict", "note": f"Saved {logged} conflict pattern(s) anonymously. No names stored."})

    return {
        "mode": mode,
        "error": error,
        "plan": {
            "summary": plan.get("summary", ""),
            "next_step": plan.get("next_step", ""),
            "drafts": plan.get("drafts", []),
        },
        "ranked": ranked,
        "help": help_by_area,
        "alerts": alerts,
        "trace": trace,
        "evidence": tools.evidence_summary(),
    }
