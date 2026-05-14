# Global Rules

These constraints apply to every agent in the pipeline.

## Output Format
- Always return structured JSON matching the Pydantic schema for your role.
- If you cannot produce valid output, return a JSON error object with `{"error": "description"}` — never return free-form text.

## Security
- Generated challenges are for authorized CTF competitions and educational use only.
- Never generate challenges that target real production systems, real user data, or real infrastructure.
- Flags must follow the format `CTF{...}` unless the user specifies a different prefix.
- Never embed the flag in plaintext in any file the player can directly access (source code comments, environment variables, HTML source). The flag should only be retrievable by solving the challenge.

## Code Quality
- All generated source code must be syntactically valid and runnable.
- Include only the intended vulnerability. No accidental bugs, no debug backdoors, no hardcoded credentials beyond what the challenge requires.
- Use standard libraries and well-known tools. Avoid obscure dependencies that make deployment fragile.

## Handoff Protocol
- Each agent receives the full pipeline state and must not discard or overwrite fields set by previous agents.
- If your output depends on a previous agent's field being populated, validate that it exists before proceeding.
