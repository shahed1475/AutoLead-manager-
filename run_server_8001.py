"""Temp launcher — port 8000 is taken by another local app, so AutoLead runs on 8001."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host      = "127.0.0.1",
        port      = 8001,
        log_level = "info",
        reload    = False,
    )
