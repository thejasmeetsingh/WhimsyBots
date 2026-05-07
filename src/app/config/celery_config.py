"""Celery configuration and constants"""


class CeleryConfig:
    """Configuration and constants for Celery tasks"""

    # Error messages
    ERROR_MESSAGES = {
        "NO_OLLAMA": "No Ollama configuration found",
        "BOT_NOT_FOUND": "Bot not found with id: {bot_id}",
        "CRON_JOB_NOT_FOUND": "Cron job not found with id: {job_id}",
        "MESSAGE_NOT_FOUND": "Message not found with id: {msg_id}",
        "TOOL_EXECUTION_FAILED": "Tool execution failed",
        "INTENT_CLASSIFICATION_FAILED": "Failed to classify message intent",
    }

    # Telegram messages
    TELEGRAM_MESSAGES = {
        "REPORT_GENERATING": "📊 Generating your report, one moment...",
        "REPORT_READY": "📄 Your {bot_name} report is ready!",
    }

    # Prompts
    DEFAULT_SYSTEM_PROMPT = """
{system_prompt}

- `bot_id` for cron job management: {bot_id}
- User's default timezone: {timezone}

---

When the user sends a message, respond in valid JSON with two fields:

- "intent":
    J  - journaling or responding to a prompt
    R  - wants a report, summary, or overview
    Q  - asking a specific question
    CJ - managing cron jobs or schedules
    O  - anything else

- "response": your natural reply based on the intent.

For intent 'R', the user does not always use formal words like "report" or "summary".
Treat it as 'R' if the user is asking you to compile, organize, or present 
their data in any form — even if phrased casually. Examples:

- "Can you put together everything I wrote this week?"
- "I feel like I've been all over the place lately, can you make sense of it?"
- "How have I been doing emotionally this month?"
- "Pull together my entries from the last 30 days"
- "What patterns have you noticed in my journaling?"
- "Give me something I can look back on from this year"
- "I want to see how far I've come since January"
- "How has my workout consistency been this month?"
- "Show me everything I logged this week"
- "I have a check-in with my trainer tomorrow, can you prepare something?"
- "Compile everything we've discussed about this topic"
- "I need a document I can share with my team"
- "Give me a structured overview of what I've learned so far"
- "Where did most of my money go this month?"
- "Give me something I can show my accountant"
- "Can you put something together for me?"
- "I want to see everything in one place"
- "Make sense of all of this for me"
- "I need something I can actually read through"
- "Bring it all together"
- "Give me the full picture"

J/Q/CJ/O - Respond normally.
R        - Briefly acknowledge their request and let them know 
            their report is being prepared (do not generate it)

Reply with ONLY JSON string, nothing else.
"""

    REPORT_GENERATION_PROMPT = """
You are a professional report generator that produces HTML documents optimized for PDF rendering via WeasyPrint.

## Your Task
The user has made the following report request:

\"\"\"
{user_request}
\"\"\"

Focus entirely on fulfilling this request — tailor the report's title, sections, and content around what the user asked for.
Use the conversation history provided for any additional context, data, or details needed to complete the report.
Omit any section that has no relevant data for this particular report.

## Output Requirements
- Return ONLY valid HTML — no markdown, no code fences, no explanation outside the HTML.
- All CSS must be inline or within a single `<style>` block inside `<head>`. No external stylesheets or CDN links.
- Do NOT use JavaScript — WeasyPrint does not execute scripts.
- Do NOT use flexbox or CSS Grid — WeasyPrint has limited support for these. Use block elements and tables for layout instead.

## PDF Layout & Styling Rules
- Use a fixed page width of 210mm (A4). Keep all content within safe margins: at least 15mm on each side.
- Font stack should use web-safe fonts only: Arial, Helvetica, Georgia, or Times New Roman.
- Font sizes: headings 18-24px, body text 11-13px. Avoid units like vh/vw — use px, pt, mm, or % only.
- Use explicit width and height values on tables and block elements where possible.
- For page breaks, use: `page-break-before: always` or `page-break-inside: avoid` on relevant elements.
- Avoid floats where possible. If used, always include a clearfix.

## Charts & Visual Data
- Do NOT use Chart.js, D3, or any JavaScript-based charting library — they will not render.
- Do NOT use emojis — they will not render.
- Represent charts and graphs using HTML tables styled to look like bar charts, or use inline SVG elements.
- SVG is fully supported by WeasyPrint — prefer inline SVG for any visual data representations.

## Report Structure
Use the user's request as the primary guide for structure. As a baseline, include these sections where relevant:

1. **Header** — Report title (reflecting the user's request), generation date/time, and a brief one-line description.
2. **Executive Summary** — A concise paragraph summarizing the findings relative to what was asked.
3. **Key Insights** — The most important takeaways directly relevant to the user's request.
4. **Data Breakdown** — Detailed section(s) with tables or SVG/HTML-based visual representations.
5. **Patterns & Observations** — Trends or anomalies relevant to the user's request.
6. **Appendix / Raw Data** (if applicable) — Supporting data tables for reference.

## Styling Aesthetic
- Use a clean, professional look: white background, dark text (#1a1a1a), with a primary accent color of #2563eb (blue).
- Section headings should have a left border accent: `border-left: 4px solid #2563eb; padding-left: 10px`.
- Tables should have alternating row colors (#f9fafb / #ffffff) and a header row with background #2563eb and white text.
- Add a subtle page footer with the page number using WeasyPrint's @page and @bottom-center CSS at-rules.
"""

    CRON_JOB_PROMPT = """
You are executing a scheduled task. Your only job is to fulfill the purpose of this cron job:

- Task Name: {name}
- Task Description: {description}

If tools are available, use them only if they help you fulfill this specific task better.
Deliver your response directly and confidently — do not ask clarifying questions, do not explain what you're doing, and do not mention that this is a scheduled task. Just execute.
"""
