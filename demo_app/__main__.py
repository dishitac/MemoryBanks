"""Start the demo page: python -m demo_app (run the purchasing service on 8001 first)."""

import uvicorn

from memorybank.config import get_settings

if __name__ == "__main__":
    uvicorn.run("demo_app.app:app", port=get_settings().api_port, workers=1)
