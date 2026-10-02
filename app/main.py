from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.api.chat import router as chat_router


class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"


app = FastAPI(
    title="PilgrimAI",
    default_response_class=UTF8JSONResponse,
)

app.include_router(chat_router)


@app.get("/health")
def health():
    return {"status": "ok"}