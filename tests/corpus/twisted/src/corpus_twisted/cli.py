"""twisted: the default reactor, twisted.web server and Agent, Deferreds and the plugin system."""

from twisted import plugins
from twisted.cred.strcred import ICheckerFactory
from twisted.internet import defer, task
from twisted.internet.defer import inlineCallbacks
from twisted.plugin import getPlugins
from twisted.python.usage import Options  # noqa: F401
from twisted.web import resource, server
from twisted.web.client import Agent, readBody
from twisted.web.iweb import IBodyProducer  # noqa: F401


class Hello(resource.Resource):
    isLeaf = True

    def render_GET(self, request):
        return b"hello from twisted"


def chain() -> defer.Deferred:
    deferred = defer.succeed(2)
    deferred.addCallback(lambda value: value * 5)
    deferred.addCallback(lambda value: defer.succeed(value + 1))
    deferred.addCallback(lambda value: print("deferred", value))
    return deferred


@inlineCallbacks
def run(reactor):
    yield chain()
    port = reactor.listenTCP(0, server.Site(Hello()), interface="127.0.0.1")
    try:
        agent = Agent(reactor)
        response = yield agent.request(b"GET", f"http://127.0.0.1:{port.getHost().port}/".encode())
        print("web", response.code, (yield readBody(response)))
    finally:
        yield port.stopListening()
    print("reactor", type(reactor).__name__)
    names = sorted(plugin.authType for plugin in getPlugins(ICheckerFactory, plugins))
    print("plugins", names)


def main() -> None:
    task.react(run)
