#!/usr/bin/env python
# vim: fileencoding=utf8:et:sta:ai:sw=4:ts=4:sts=4

import sys
import traceback

import greenhouse
import junction

HOST = "127.0.0.1"
PORT = 12345

RELAY_ADDR = (HOST, 9100)
SERVICE_ADDR = (HOST, 9000)

SERVICE = 1


greenhouse.global_exception_handler(traceback.print_exception)


def main():
    peer_addr = RELAY_ADDR if '-r' in sys.argv else SERVICE_ADDR

    client = junction.Client(peer_addr)

    client.connect()
    if not client.wait_connected(timeout=3):
        raise RuntimeError("connection timeout")

    print client.rpc(SERVICE, 0, "echo", ('one',), {})

    rpcs = map(lambda msg: client.send_rpc(SERVICE, 0, "echo", (msg,), {}),
            ('two', 'three', 'four', 'five'))
    while rpcs:
        rpc = junction.wait_any(rpcs)
        rpcs.remove(rpc)
        print rpc.value

if __name__ == '__main__':
    main()
