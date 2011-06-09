from __future__ import absolute_import

import weakref

from greenhouse import scheduler
import mummy
from .core import const
from . import errors


class RPC(object):
    """A representation of a single RPC request/response cycle

    instances of this class shouldn't be created directly, they are returned by
    :meth:`Node.send_rpc() <junction.node.Node.send_rpc>`.
    """
    def __init__(self, client, counter, target_count):
        self._client = client
        self._completed = False
        self._waits = []
        self._results = []
        self._children = []

        self._counter = counter
        self._target_count = target_count

    def wait(self, timeout=None):
        """Block the current greenlet until the response arrives

        :param timeout:
            the maximum number of seconds to wait before raising a
            :class:`WaitTimeout <junction.errors.WaitTimeout>`. the default of
            None allows it to wait indefinitely.
        :type timeout: int, float or None

        :returns: a list of the responses returned by the RPC's target peers.

        :raises:
            :class:`WaitTimeout <junction.errors.WaitTimeout>` if ``timeout``
            is supplied and runs out before the response arrives.
        """
        self._client.wait(self, timeout)
        return self.results

    def after(self, func=None, other_parents=None):
        """Schedule ``func`` to run after the RPC has completed

        :param func:
            a callback to run after this RPC and any others in
            ``other_parents`` have completed. the callback should take as many
            arguments as the Dependent has parents (one plus the length of
            other_parents), and each argument will be one parent's results. The
            callback's return value can be an :class:`RPC` instance, in which
            case waiting on the Dependent will also wait for that RPC to
            complete, or any other object, which will appear as the
            :attr:`results <Dependent.results>` attribute
        :type func: function

        :param other_parents:
            any combination of other :class:`RPC`\s and :class:`Dependent`\s
            that should be waited on for completion before firing the callback
        :type other_parents: list or None

        :returns:
            a :class:`Dependent`, which can be waited on, waited on as part of
            a group of other :class:`RPC`\s and :class:`Dependent`\s with
            :meth:`Node.wait_any <junction.node.Node.wait_any>`
        """
        if func is None:
            return lambda f: self.after(f, other_parents)

        parents = [self] + (other_parents or [])
        counter = self._client.next_counter()

        op = Dependent(self._client, counter, parents, func)
        self._children.append(weakref.ref(op))
        self._client.rpcs[counter] = op

        return op

    def abort(self, result):
        """stop any pending action and set the result

        this method will also abort all of its children and further descendents
        with the same result

        :param result: the object to which to hard-code the RPC's results

        :raises:
            :class:`AlreadyComplete <junction.errors.AlreadyComplete>` if the
            RPC is already complete.
        """
        if self._completed:
            raise errors.AlreadyComplete()

        self._completed = True
        self._target_count = 1
        self._results = [result]

        for wait in self._waits:
            wait.finish(self)
        self._waits = []

        for child in self._children:
            child = child()
            if child is None:
                continue

            child.abort(self, result)
        self._children = []

    @property
    def counter(self):
        return self._counter

    @property
    def target_count(self):
        "The number of peer nodes that will return a response for this RPC"
        return self._target_count

    @property
    def partial_results(self):
        """The results that the RPC has received so far

        this may also be the complete results, if :attr:`complete` is True
        """
        return [type(x)(*deepcopy(x.args)) if isinstance(x, Exception)
                else deepcopy(x) for x in self._results]

    @property
    def results(self):
        """The RPC's response, if it has arrived

        :attr:`complete` indicates whether the result is available or not, if
        not then this attribute raises AttributeError.
        """
        if not self._completed:
            raise AttributeError("incomplete response")
        return self.partial_results

    @property
    def complete(self):
        "Whether the RPC's response has arrived yet."
        return self._completed

    def _incoming(self, peer_ident, rc, result):
        self._results.append(self._format_result(peer_ident, rc, result))

    def _complete(self):
        if self._completed:
            return
        self._completed = True

        for wait in self._waits:
            wait.finish(self)
        self._waits = []

        for child in self._children:
            child = child()
            if child is None:
                continue

            child._incoming(self, self.results)
        self._children = []

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

        if rc == const.RPC_ERR_LOST_CONN:
            return errors.LostConnection(peer_ident)

        return errors.UnrecognizedRemoteProblem(peer_ident, rc, result)


class Dependent(object):
    """A function wrapper queued up to run after the termination of RPCs

    instances of this class shouldn't be created directly, they are returned by
    :meth:`RPC.after` and :meth:`Dependent.after`
    """
    def __init__(self, client, counter, parents, func):
        self._client = client
        self._counter = counter
        self._parents = parents
        self._parent_results = [None] * len(parents)
        self._children = []
        self._func = func
        self._completed = False
        self._waits = []
        self._result = None
        self._errored = False

        complete = filter(lambda p: p.complete, self._parents)
        for parent in complete:
            self._incoming(parent, parent.results)

    def wait(self, timeout=None):
        """Block the current greenlet until the operation has completed

        :param timeout:
            the maximum number of seconds to wait before raising a
            :class:`WaitTimeout <junction.errors.WaitTimeout>`, the default of
            None allows it to wait indefinitely.
        :type timeout: int, float or None

        :returns:
            The return value of the operation's function, unless it returned a
            :class:`RPC`, in which case it returns the list of that RPC's
            results

        :raises:
            :class:`WaitTimeout <junction.errors.WaitTimeout>` if ``timeout``
            is supplied and runs out before the response arrives.
        """
        self._client.wait(self, timeout)
        return self.results

    def after(self, func=None, other_parents=None):
        """Schedule ``func`` to run after the Dependent has completed

        :param func:
            a callback to run after this Dependent and any others in
            ``other_parents`` have completed. the callback should take as many
            arguments as the new Dependent has parents (one plus the length of
            other_parents), and each argument will be one parent's results. The
            callback's return value can be an :class:`RPC` instance, in which
            case waiting on the Dependent will also wait for that RPC to
            complete, or any other object which will then appear as the
            :attr:`results <Dependent.results>` attribute
        :type func: function
        :param other_parents:
            any combination of other :class:`RPC`\s and :class:`Dependent`\s
            that should be waited on for completion before firing the callback
        :type other_parents: list or None

        :returns:
            a :class:`Dependent`, which can be waited on, waited on as part of
            a group of other :class:`RPC`\s and :class:`Dependent`\s with
            :meth:`Node.wait_any <junction.node.Node.wait_any>`
        """
        if func is None:
            return lambda f: self.after(f, other_parents)

        parents = [self] + (other_parents or [])
        counter = self._client.next_counter()

        op = Dependent(self._client, counter, parents, func)
        self._children.append(weakref.ref(op))
        self._client.rpcs[counter] = op

        return op

    def abort(self, result):
        """stop any pending action and set the result

        this method will also abort all of its children and further descendents
        with the same result

        :param result: the object to which to hard-code the Dependent's results

        :raises:
            :class:`AlreadyComplete <junction.errors.AlreadyComplete>` if the
            Dependent is already complete.
        """
        if self._completed:
            raise errors.AlreadyComplete()

        self._result = result
        self._completed = True
        self._errored = True

        for wait in self._waits:
            wait.finish(self)
        self._waits = []

        for child in self._children:
            child = child()
            if child is None:
                continue
            child.abort(result)
        self._children = []

    @property
    def results(self):
        """the results of the callback or of the RPC the callback produced

        :attr:`complete` indicates whether the result is available or not, if
        not then this attribute raises AttributeError.
        """
        if not self._completed:
            raise AttributeError("incomplete operation")
        if isinstance(self._result, RPC):
            return self._result.results
        return self._result

    @property
    def complete(self):
        "Whether the Dependent (and possibly its resulting RPC) has completed"
        if not self._completed:
            return False
        if isinstance(self._result, RPC):
            return self._result.complete
        return True

    def _incoming(self, parent, result):
        index = self._parents.index(parent)
        self._parents[index] = None
        self._parent_results[index] = result

        if all(p is None for p in self._parents):
            self._complete()

    def _func_runner(self):
        if self._completed:
            return
        self._completed = True

        try:
            self._result = self._func(*self._parent_results)
        except Exception:
            self.abort(errors.DependentCallbackException(
                    traceback.format_exception(*sys.exc_info())))

        self._parent_result = self._parents = None

        if (not isinstance(self._result, RPC)) or self._result.complete:
            for wait in self._waits:
                wait.finish(self)

            for child in self._children:
                child = child()
                if child is None:
                    continue

                child._incoming(self, self.results)
        else:
            for wait in self._waits:
                wait.transfer(self, self._result)
            for child in self._children:
                child = child()
                if child is None:
                    continue

                child._transfer(self, self._result)
                self._result._children.append(weakref.ref(child))

        self._waits = []
        self._children = []

    def _complete(self):
        if self._completed:
            return
        # at this point we are in a connection's receive coro. if the
        # user-provided function is stupid and blocks, it could potentially
        # wait on something that would need to come back through the connection
        # whose receiver coro it just blocked, resulting in deadlock. so run
        # the callback in a separate coro just in case.
        scheduler.schedule(self._func_runner)

    def _transfer(self, source, target):
        for i, parent in enumerate(self._parents):
            if parent is source:
                self._parents[i] = target
                if target.complete:
                    self._incoming(target, target.results)
                break


def deepcopy(item):
    return mummy.loads(mummy.dumps(item))