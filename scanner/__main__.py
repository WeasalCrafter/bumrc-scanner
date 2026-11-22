"""Start the scanner UI:  python -m scanner   (then open http://localhost:8080)"""
import uvicorn

from scanner.config import load_env
from scanner.web.app import create_app

if __name__ == "__main__":
    env = load_env()
    uvicorn.run(create_app(env), host=env.get("HOST", "127.0.0.1"), port=int(env.get("PORT", "8080")))
