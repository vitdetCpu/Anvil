import json
import os
import time as _time
from anthropic import Anthropic


def _get_client():
    api_key = os.environ.get("MINIMAX_API_KEY")
    if not api_key:
        raise RuntimeError("MINIMAX_API_KEY not set")
    return Anthropic(api_key=api_key, base_url="https://api.minimax.io/anthropic")


MODEL = "MiniMax-M2.7-highspeed"
JUDGE_MODEL = "MiniMax-M2.7-highspeed"
THINKING_BUDGET = 1000


def _call_llm_streaming(system, messages, max_tokens=1024, on_thinking=None, on_output_start=None):
    """Call with extended thinking, streaming thinking deltas.
    Returns (parsed_json, full_thinking_text).
    on_thinking(delta_text) is called for each thinking chunk.
    on_output_start() is called once when the model starts generating output.
    Retries once on JSON parse failure or rate limit."""
    client = _get_client()
    total_tokens = max_tokens + THINKING_BUDGET

    for attempt in range(2):
        thinking_text = ""
        answer_text = ""
        output_started = False
        try:
            with client.messages.stream(
                model=MODEL,
                max_tokens=total_tokens,
                system=system,
                messages=messages,
                thinking={
                    "type": "enabled",
                    "budget_tokens": THINKING_BUDGET,
                },
            ) as stream:
                for event in stream:
                    if event.type == "content_block_delta":
                        if event.delta.type == "thinking_delta":
                            thinking_text += event.delta.thinking
                            if on_thinking:
                                on_thinking(event.delta.thinking)
                        elif event.delta.type == "text_delta":
                            if not output_started and on_output_start:
                                output_started = True
                                on_output_start()
                            answer_text += event.delta.text
        except Exception as e:
            error_str = str(e).lower()
            if "rate" in error_str or "429" in error_str:
                if attempt == 0:
                    _time.sleep(5)
                    continue
            raise

        content = answer_text.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            return json.loads(content), thinking_text
        except json.JSONDecodeError:
            if attempt == 0:
                # Build content blocks for retry
                blocks = []
                if thinking_text:
                    blocks.append({"type": "thinking", "thinking": thinking_text})
                blocks.append({"type": "text", "text": answer_text})
                messages.append({"role": "assistant", "content": blocks})
                messages.append(
                    {"role": "user", "content": "That was not valid JSON. Please return ONLY a valid JSON object, no other text."}
                )
    raise ValueError(f"LLM returned invalid JSON after retry: {content[:200]}")


def _call_llm_fast(system, messages, max_tokens=1024):
    """Fast call using Haiku, no extended thinking. Returns (parsed_json, '')."""
    client = _get_client()

    for attempt in range(2):
        try:
            response = client.messages.create(
                model=JUDGE_MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                temperature=0.3,
                timeout=30,
            )
        except Exception as e:
            error_str = str(e).lower()
            if "rate" in error_str or "429" in error_str:
                if attempt == 0:
                    _time.sleep(5)
                    continue
            raise
        # Find the text block (skip any thinking blocks)
        content = ""
        for block in response.content:
            if hasattr(block, "text"):
                content = block.text.strip()
                break
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            return json.loads(content), ""
        except json.JSONDecodeError:
            if attempt == 0:
                messages.append({"role": "assistant", "content": content})
                messages.append(
                    {"role": "user", "content": "That was not valid JSON. Please return ONLY a valid JSON object, no other text."}
                )
    raise ValueError(f"LLM returned invalid JSON after retry: {content[:200]}")


def _build_history_context(history):
    """Build a condensed history string for agent context."""
    lines = []
    for entry in history:
        r = entry["round"]
        atk = entry.get("red_attack", {})
        success = entry.get("attack_success", False)
        patch = entry.get("blue_patch", {})
        lines.append(
            f"Round {r}: RED used {atk.get('vulnerability_type', '?')} "
            f"({'SUCCESS' if success else 'BLOCKED'}). "
            f"BLUE patched: {patch.get('vuln_fixed', 'N/A')}."
        )
    return "\n".join(lines) if lines else "No previous rounds."


RED_SYSTEM = """You are RED, an offensive security AI in a live competition against BLUE, a defensive AI.

Your job: find vulnerabilities in a Flask web app and craft curl commands to exploit them.
BLUE patches the app after each of your attacks. You must find NEW vulnerabilities that BLUE hasn't fixed yet.
Be creative — your opponent is skilled.

You MUST respond with ONLY a JSON object in this exact format:
{
  "curl_command": "curl -X POST http://localhost:5050/login -H 'Content-Type: application/json' -d '{...}'",
  "vulnerability_type": "SQL Injection",
  "explanation": "Brief explanation of the attack",
  "expected_impact": "What the attack should achieve"
}

Rules:
- Target ONLY http://localhost:5050
- Use real curl syntax with -X, -H, -d, -b flags
- Do NOT repeat attacks that were already blocked
- Try different vulnerability classes each round"""

BLUE_SYSTEM = """You are BLUE, a defensive security AI in a live competition against RED, an offensive AI.

Your job: patch vulnerabilities in a Flask web app after RED attacks it.
RED will try to bypass your fixes next round, so be thorough.

You MUST respond with ONLY a JSON object in this exact format:
{
  "file_content": "the COMPLETE patched app.py file content as a string",
  "explanation": "What vulnerability you fixed and how",
  "vuln_fixed": "Short label like 'SQL injection in login'"
}

Rules:
- Return the COMPLETE app.py file — not a diff, not a snippet
- Keep all existing functionality working
- Only fix the vulnerability that was just exploited
- Do NOT add unnecessary features or imports
- The app must remain runnable with: python target_app/app.py"""

RED_JUDGE_SYSTEM = """You are judging whether a security attack succeeded.
Given the HTTP response from the server, determine if the attack achieved its intended impact.

Respond with ONLY a JSON object:
{
  "success": true or false,
  "evidence": "Brief explanation of why the attack succeeded or failed"
}"""


def call_red_agent(app_source, history, on_thinking=None, on_output_start=None):
    """Ask Red to craft an attack. Returns (dict, thinking_text).
    on_thinking(delta) called for each thinking chunk."""
    history_ctx = _build_history_context(history)
    messages = [
        {
            "role": "user",
            "content": (
                f"## Current app.py source:\n```python\n{app_source}\n```\n\n"
                f"## Battle history:\n{history_ctx}\n\n"
                f"Craft your next attack."
            ),
        },
    ]
    return _call_llm_streaming(RED_SYSTEM, messages, max_tokens=1024, on_thinking=on_thinking, on_output_start=on_output_start)


def call_blue_agent(app_source, attack, attack_response, history, on_thinking=None, on_output_start=None):
    """Ask Blue to patch the app. Returns (dict, thinking_text).
    on_thinking(delta) called for each thinking chunk."""
    history_ctx = _build_history_context(history)
    messages = [
        {
            "role": "user",
            "content": (
                f"## Current app.py source:\n```python\n{app_source}\n```\n\n"
                f"## RED's attack this round:\n"
                f"Type: {attack.get('vulnerability_type', '?')}\n"
                f"Command: {attack.get('curl_command', '?')}\n"
                f"Explanation: {attack.get('explanation', '?')}\n\n"
                f"## Server response to the attack:\n"
                f"Status: {attack_response.get('status_code', '?')}\n"
                f"Body: {attack_response.get('body', '?')[:500]}\n\n"
                f"## Battle history:\n{history_ctx}\n\n"
                f"Patch the vulnerability."
            ),
        },
    ]
    return _call_llm_streaming(BLUE_SYSTEM, messages, max_tokens=4096, on_thinking=on_thinking, on_output_start=on_output_start)


def call_red_judge(attack, response_body, status_code):
    """Ask Haiku to judge if the attack succeeded. Returns (dict, '')."""
    messages = [
        {
            "role": "user",
            "content": (
                f"Attack type: {attack.get('vulnerability_type', '?')}\n"
                f"Expected impact: {attack.get('expected_impact', '?')}\n"
                f"Server status code: {status_code}\n"
                f"Server response body: {response_body[:1000]}\n\n"
                f"Did the attack succeed?"
            ),
        },
    ]
    return _call_llm_fast(RED_JUDGE_SYSTEM, messages, max_tokens=2048)


SUMMARY_SYSTEM = """You are a security consultant summarizing a red team vs blue team exercise.
Given the battle history, produce a concise post-mortem.

Respond with ONLY a JSON object:
{
  "recommendations": ["actionable recommendation 1", "recommendation 2", "..."],
  "remaining_risks": ["risk that was not addressed", "..."]
}

Rules:
- 3-5 specific, actionable recommendations
- 2-3 remaining risks the blue team didn't address
- Be concise — one sentence each"""


def call_summary_agent(original_source, final_source, history):
    """Generate post-battle security recommendations. Returns (dict, '')."""
    rounds_summary = []
    for entry in history:
        rounds_summary.append(
            f"- Round {entry['round']}: RED tried {entry['red_attack'].get('vulnerability_type', '?')} "
            f"({'SUCCESS' if entry['attack_success'] else 'BLOCKED'}). "
            f"BLUE fixed: {entry['blue_patch'].get('vuln_fixed', 'N/A')}."
        )
    messages = [
        {
            "role": "user",
            "content": (
                f"## Original codebase:\n```python\n{original_source}\n```\n\n"
                f"## Final codebase:\n```python\n{final_source}\n```\n\n"
                f"## Battle rounds:\n" + "\n".join(rounds_summary) + "\n\n"
                f"Provide your security recommendations."
            ),
        },
    ]
    return _call_llm_fast(SUMMARY_SYSTEM, messages, max_tokens=2048)
