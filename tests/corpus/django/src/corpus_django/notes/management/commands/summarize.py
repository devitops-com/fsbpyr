from django.core.management.base import BaseCommand
from django.template.loader import render_to_string

from corpus_django.notes.models import Note


class Command(BaseCommand):
    help = "Prints the notes through a template."

    def handle(self, *args, **options):
        notes = list(Note.objects.order_by("id"))
        self.stdout.write(
            render_to_string("notes/summary.txt", {"notes": notes, "count": len(notes)}).strip()
        )
