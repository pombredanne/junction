import logging
import random


log = logging.getLogger("junction.hooks")


def select_peer(peer_addrs, service, routing_id, method):
    '''Choose a target from the available peers for a singular RPC

    :param peer_addrs:
        the ``(host, port)``s of the peers eligible to handle the RPC, and
        possibly a ``None`` entry if this hub can handle it locally
    :type peer_addrs: list
    :param service: the service of the RPC
    :type service: anything hash-able
    :param routing_id: the routing_id of the RPC
    :type routing_id: int
    :param method: the RPC method name
    :type method: string

    :returns: one of the provided peer_addrs

    There is no reason to call this method directly, but it may be useful to
    override it in a Hub subclass.

    This default implementation uses ``None`` if it is available (prefer local
    handling), then falls back to a random selection.
    '''
    if any(p is None for p in peer_addrs):
        return None
    return random.choice(peer_addrs)

def connection_lost(hub, peer, subscriptions):
    '''A connection has gone down unexpectedly

    :param hub: the hub that owned the connection
    :type hub: :class:`Hub<junction.hub.Hub>`
    :param peer: the ``(host, port)`` with which the peer identified itself
    :type peer: (host, port) tuple
    :param subscriptions:
        the subscriptions the peer had when it went down; this is a list of
        four-tuples of ``(msg_type, service, mask, value)`` where msg_type
        may be 4 for publish, or 5 for rpc (these constants are found in
        ``junction.core.const``).
    '''
    pass


def _get(hooks, name):
    log.info("invoking hook %s" % name)

    default = globals()[name]
    hook = getattr(hooks, name, None)
    if hook is None:
        return default

    def handler(*args, **kwargs):
        try:
            return hook(*args, **kwargs)
        except Exception, exc:
            log.error("exception in hook %s: %r. falling back to default" %
                    (name, exc))
            return default(*args, **kwargs)
    return handler
