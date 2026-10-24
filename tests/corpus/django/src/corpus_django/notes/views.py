from typing import ClassVar

from rest_framework import serializers, viewsets

from corpus_django.notes.models import Note


class NoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Note
        fields: ClassVar[list[str]] = ["id", "title", "body"]


class NoteViewSet(viewsets.ModelViewSet):
    queryset = Note.objects.order_by("id")
    serializer_class = NoteSerializer
