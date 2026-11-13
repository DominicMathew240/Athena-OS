# Test credentials (preview only)

Login page: /login/ (Django auth, username + password)

| Role | Username | Password |
|---|---|---|
| Superuser (all access) | inspector | Athena!Inspect2026 |
| Regular user (default "labeler" role, can run inspections) | fieldtech | Athena!Field2026 |

Pre-existing accounts `admin` and `operator` exist with unknown passwords; do not change them.

AI model: no built-in Qwen server is configured (ATHENA_VLM_BASE_URL is blank), so the app shows the "Connect your AI first" journey until a user saves their own OpenAI-compatible endpoint on /settings/ai.
