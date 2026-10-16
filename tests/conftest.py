import os

import pytest

# Tests never read the local `.env`: environment variables take precedence over it, and
# importing `app.main` builds the app from them. No test talks to the real Langfuse.
os.environ.update(
    {
        "LANGFUSE_PUBLIC_KEY": "pk-lf-test",
        "LANGFUSE_SECRET_KEY": "sk-lf-test",
        "LANGFUSE_HOST": "https://langfuse.test",
        "CORS_ALLOWED_ORIGINS": "[]",
    }
)


@pytest.fixture
def anyio_backend() -> str:
    """Run async tests on asyncio only."""
    return "asyncio"
