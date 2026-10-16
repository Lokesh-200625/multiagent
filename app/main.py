import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.api.chat import router as chat_router
from app.api.transit import router as transit_router


class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"


app = FastAPI(
    title="PilgrimAI",
    default_response_class=UTF8JSONResponse,
)

app.include_router(chat_router)
app.include_router(transit_router)


@app.get("/health")
def health():
    return {"status": "ok"}
