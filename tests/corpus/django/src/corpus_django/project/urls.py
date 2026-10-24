from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from corpus_django.notes.views import NoteViewSet

router = DefaultRouter()
router.register("notes", NoteViewSet)

urlpatterns = [path("admin/", admin.site.urls), path("api/", include(router.urls))]
