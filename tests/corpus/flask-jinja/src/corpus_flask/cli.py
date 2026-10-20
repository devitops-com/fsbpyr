"""A Flask app with templates and static files next to its package, run through the test client."""

from flask import Flask, render_template

app = Flask(__name__)


@app.get("/<name>")
def hello(name: str) -> str:
    return render_template("hello.html", name=name)


def main() -> None:
    client = app.test_client()
    print(client.get("/Ada").get_data(as_text=True).strip())
    print(client.get("/static/note.txt").get_data(as_text=True).strip())
    print(client.get("/missing").status)
