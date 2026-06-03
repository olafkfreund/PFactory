import uvicorn
from gateway import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8400, log_level="warning")
