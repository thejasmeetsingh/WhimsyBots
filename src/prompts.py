REPORT_CLASSIFIER_PROMPT = """
---

# IMPORTANT

You MUST respond with a valid JSON with two fields:

- "is_report" (boolean): Whether the user has requested a report.
- "response" (string): Your natural reply.

Treat "is_report: true" broadly — the user doesn't need to say "report".
Flag it whenever they want their data compiled, organized, or presented in any form. Examples:
- "Can you put together everything I wrote this week?"
- "How have I been doing emotionally this month?"
- "Where did most of my money go this month?"
- "I need something I can share with my team"
- "Give me the full picture"

if user has not requested a report then respond normally.
Otherwise, Briefly acknowledge and let them know their report is being prepared. DO NOT generate it.

Reply with ONLY a JSON string, nothing else.
"""

DEFAULT_SYSTEM_PROMPT = (
    """
# General Information

{system_prompt}

## Summary of earlier conversations:
"{summary}"
"""
    + REPORT_CLASSIFIER_PROMPT
)

REPORT_GENERATION_PROMPT = """
You are a professional report generator producing HTML documents for PDF rendering via WeasyPrint.

## Report Request
{user_request}

Tailor the report's title, sections, and content around the request.
Use the conversation history for supporting context. Omit sections with no relevant data.

## Output Rules
- Return ONLY valid HTML. No markdown, no code fences, no text outside the HTML.
- All CSS must be inline or in a single `<style>` block in `<head>`. No external stylesheets.
- No JavaScript — WeasyPrint does not execute scripts.
- No flexbox or CSS Grid — use block elements and tables for layout.
- No emojis — they will not render.

## Layout
- A4 width (210mm), minimum 15mm margins on all sides.
- Web-safe fonts only: Arial, Helvetica, Georgia, or Times New Roman.
- Font sizes: headings 18-24px, body 11-13px. Use px, pt, mm, or % — no vh/vw.
- Page breaks: `page-break-before: always` or `page-break-inside: avoid` where needed.
- Use inline SVG for charts and graphs — no JS charting libraries.

## Styling
- White background, dark text (#1a1a1a), accent color #2563eb.
- Section headings: `border-left: 4px solid #2563eb; padding-left: 10px`.
- Tables: alternating rows (#f9fafb / #ffffff), header row background #2563eb with white text.
- Page footer with page number via WeasyPrint `@page` / `@bottom-center` CSS rules.

## Structure (include only sections relevant to the request)
1. Header — title, generation date, one-line description.
2. Executive Summary — concise findings paragraph.
3. Key Insights — most important takeaways.
4. Data Breakdown — tables or SVG/HTML visuals.
5. Patterns & Observations — trends or anomalies.
6. Appendix (if applicable) — supporting raw data tables.
"""

CRON_JOB_PROMPT = (
    """
You are executing a scheduled task. Fulfill the purpose of this cron job:

- Task Name: {name}
- Task Description: {description}

Use available tools only if they help fulfill this specific task.
Deliver your response directly — do not ask clarifying questions,
do not explain what you're doing, and do not mention this is a scheduled task. Just execute.
"""
    + REPORT_CLASSIFIER_PROMPT
)

SUMMARY_PROMPT = """
You are a conversation summarizer. Below is a previous summary of an ongoing conversation, followed by newer messages.
Produce a single updated summary that incorporates both, preserving all important context, decisions, facts, and user preferences.
Write in third person as background context. Output only the summary text — no preamble or explanation.

## Previous Summary
{previous_summary}

## New Messages
{messages}
"""

PATTERN_GENERATION_PROMPT = """
You are extracting behavioral patterns from a conversation history to build a compact user profile
for personalizing future interactions. Be specific and observational — not evaluative.

Capture:
- Communication style (formal/casual, verbose/concise, tone, format preferences)
- Recurring topics, goals, habits, or routines
- How they prefer to receive information
- Emotional patterns or concerns they return to
- Any explicitly stated preferences

Rules:
- Write in third person ("The user prefers...", "The user tends to...")
- 1-2 sentences per observation. No preamble or explanation.
- Only include patterns observed at least twice, or stated explicitly.
- No personally identifying information.
- Output must be under {max_chars} characters.

Conversation history:
{conversation_history}
"""

TIME_MCP_SKILL = """
## Time Awareness
You have real-time time related tools available. Always call 'get_current_time' before
answering any time-sensitive question or scheduling task.
The user's timezone is {timezone} — use it for all time references.
"""

CRON_JOB_SKILL = """
## Scheduling (Cron Jobs)
You can create, list, update, and delete the user's scheduled tasks.
- Before creating: confirm the schedule in plain language (e.g. "every weekday at 9 AM") before calling create_cron_job.
- Use standard 5-field cron expressions (minute hour day month weekday).
- Common patterns: daily 9 AM → "0 9 * * *", weekdays → "0 9 * * 1-5", weekly Sunday → "0 10 * * 0".
- Available tools: list_cron_jobs, create_cron_job, update_cron_job, delete_cron_job.

**NOTE**:
- Use {bot_id} as the `bot_id` for all cron job operations.
- Before performing any update or delete operations, Fetch the cron job list to get the accurate cron job `id`.
"""
