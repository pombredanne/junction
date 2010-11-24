from __future__ import absolute_import

import weakref

from greenhouse import utils
from . import const, errors


class Client(object):
    def __init__(self):
        self.counter = 1
        self.inflight = {}
        self.rpcs = weakref.WeakValueDictionary()

    def request(self, targets, service, method, routing_id, args, kwargs):
        counter = self.counter
        self.counter += 1
        target_set = set()

        msg = (const.MSG_TYPE_RPC_REQUEST,
                (counter, service, method, routing_id, args, kwargs))

        for peer in targets:
            target_set.add(peer.ident)
            peer.send_queue.put(msg)

        if not target_set:
            return None

        self.inflight[counter] = target_set

        rpc = RPC(self, counter)
        self.rpcs[counter] = rpc

        return rpc

    def response(self, peer, msg):
        if not isinstance(msg, tuple) or len(msg) != 3:
            # drop malformed responses
            return

        counter, rc, result = msg
        if counter not in self.inflight:
            # drop mistaken responses
            return

        targets = self.inflight[counter]
        if peer.ident not in targets:
            # again, drop mistaken responses
            return
        targets.remove(peer.ident)

        if not targets and counter in self.rpcs:
            self.rpcs[counter]._complete(peer.ident, rc, result)

    def wait(self, rpc_list, timeout=None):
        if not hasattr(rpc_list, "__iter__"):
            rpc_list = [rpc_list]
        else:
            rpc_list = list(rpc_list)

        for rpc in rpc_list:
            if rpc._completed:
                return rpc

        waiter = Wait(self, [r.counter for r in rpc_list])

        for rpc in rpc_list:
            rpc._waiters.append(waiter)

        if waiter.done.wait(timeout):
            raise errors.RPCWaitTimeout()

        return waiter.completed_rpc


class RPC(object):
    """A representation of a single RPC request/response cycle

    instances of this class shouldn't be created directly, they are returned by
    :meth:`Node.send_rpc() <junction.node.Node.send_rpc>`.
    """
    def __init__(self, client, counter):
        self._client = client
        self._waiters = []
        self._completed = False
        self._results = []

        self.counter = counter

    def wait(self, timeout=None):
        """Block the current greenlet until the response arrives
        
        :param timeout:
            the maximum number of seconds to wait before raising a
            :class:`RPCWaitTimeout <junction.errors.RPCWaitTimeout>`. the
            default of None allows it to wait indefinitely.
        :type timeout: int, float or None

        :returns: a list of the responses returned by the RPC's target peers.

        :raises:
            :class:`RPCWaitTimeout <junction.errors.RPCWaitTimeout>` if
            ``timeout`` is supplied and runs out before the response arrives.
        """
        self._client.wait(self, timeout)
        return self.results

    @property
    def results(self):
        """The RPC's response, if it has arrived
        
        :attr:`complete` indicates whether the result is available or not, if
        not then this attribute raises AttributeError.
        """
        if not self._completed:
            raise AttributeError("incomplete response")
        return self._results[:]

    @property
    def complete(self):
        "Whether the RPC's response has arrived yet."
        return self._completed

    def _complete(self, peer_ident, rc, result):
        self._completed = True
        self._results.append(self._format_result(peer_ident, rc, result))
        del self._client.inflight[self.counter]
        if self._waiters:
            self._waiters[0].finish(self)

    def _format_result(self, peer_ident, rc, result):
        if not rc:
            return result

        if rc == const.RPC_ERR_NOHANDLER:
            return errors.NoRemoteHandler(
                    "RPC mistakenly sent to %r" % (peer_ident,))

        if rc == const.RPC_ERR_KNOWN:
            err_code, err_args = result
            return errors.HANDLED_ERROR_TYPES.get(
                    err_code, errors.HandledError)(peer_ident, *err_args)

        if rc == const.RPC_ERR_UNKNOWN:
            return errors.RemoteException(peer_ident, result)

        return errors.UnrecognizedRemoteProblem(peer_ident, rc, result)


class Wait(object):
    def __init__(self, client, counters):
        self.client = client
        self.counters = counters
        self.done = utils.Event()
        self.completed_rpc = None

    def finish(self, rpc):
        self.completed_rpc = rpc

        rpcs = self.client.rpcs
        for counter in self.counters:
            rpc = rpcs.get(counter, None)
            if not rpc:
                continue
            rpc._waiters.remove(self)

        self.done.set()
