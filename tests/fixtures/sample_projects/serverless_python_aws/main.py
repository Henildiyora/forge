from fastapi import FastAPI

app = FastAPI()


@app.get("/")
def handler() -> dict[str, str]:
    return {"hello": "aws"}
