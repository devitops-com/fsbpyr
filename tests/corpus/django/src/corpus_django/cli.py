"""Sets up a Django project in a temporary directory and exercises its parts."""

import os
import tempfile
from pathlib import Path


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CORPUS_DB"] = str(Path(tmp, "db.sqlite3"))
        os.environ["CORPUS_STATIC_ROOT"] = str(Path(tmp, "static"))
        os.environ["DJANGO_SETTINGS_MODULE"] = "corpus_django.project.settings"
        run()


def run() -> None:
    import django
    from django.core.management import call_command, get_commands
    from django.test import Client
    from django.utils import translation
    from rest_framework.test import APIClient

    django.setup()
    call_command("migrate", verbosity=0)
    call_command("check")
    print("commands lists summarize:", get_commands().get("summarize") == "corpus_django.notes")

    client = Client()
    response = client.get("/admin/login/")
    print("admin login", response.status_code, "Django administration" in response.text)
    with translation.override("de"):
        german = client.get("/admin/login/", headers={"accept-language": "de"})
        print("admin login de", german.status_code, "Anmelden" in german.text)

    api = APIClient()
    created = api.post("/api/notes/", {"title": "first", "body": "hello"}, format="json")
    print("api post", created.status_code, created.json())
    api.post("/api/notes/", {"title": "second"}, format="json")
    listing = api.get("/api/notes/")
    print("api get", listing.status_code, [note["title"] for note in listing.json()])
    call_command("summarize")

    from io import StringIO
    out = StringIO()
    call_command("collectstatic", interactive=False, verbosity=1, stdout=out)
    copied = out.getvalue().split(maxsplit=1)[0]
    print("collectstatic", copied, "files;",
          len(list(Path(os.environ["CORPUS_STATIC_ROOT"]).rglob("*.css"))), "css")
