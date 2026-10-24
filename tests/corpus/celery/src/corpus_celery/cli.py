"""Runs tasks on an in-process Celery worker with memory broker and cache backend."""

from celery import chain, group
from celery.contrib.testing.worker import start_worker

from corpus_celery.tasks import add, app, double, label


def main() -> None:
    with start_worker(app, pool="solo", perform_ping_check=False, loglevel="CRITICAL"):
        print("add", add.delay(2, 3).get(timeout=30))
        print("chain", chain(add.s(1, 2), double.s(), label.s())().get(timeout=30))
        print("group", group(double.s(n) for n in range(4))().get(timeout=30))
        print("broker", app.connection().transport.__class__.__name__)
