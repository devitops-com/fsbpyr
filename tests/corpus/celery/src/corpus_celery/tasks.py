from celery import Celery

app = Celery("corpus", broker="memory://", backend="cache+memory://")
app.conf.update(worker_hijack_root_logger=False, broker_connection_retry_on_startup=True)


@app.task
def add(x: int, y: int) -> int:
    return x + y


@app.task
def double(x: int) -> int:
    return x * 2


@app.task
def label(x: int) -> str:
    return f"value={x}"
