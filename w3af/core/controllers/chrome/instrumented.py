"""
instrumented.py

Copyright 2018 Andres Riancho

This file is part of w3af, http://w3af.org/ .

w3af is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation version 2 of the License.

w3af is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with w3af; if not, write to the Free Software
Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

"""
from requests import ConnectionError

import w3af.core.controllers.output_manager as om

from w3af.core.controllers.chrome.devtools import DebugChromeInterface
from w3af.core.controllers.chrome.process import ChromeProcess
from w3af.core.controllers.chrome.proxy import LoggingProxy
from w3af.core.data.fuzzer.utils import rand_alnum


class InstrumentedChrome(object):
    """
    1. Start a proxy server
    2. Start a chrome process that navigates via the proxy
    3. Load a page in Chrome (via the proxy)
    4. Receive Chrome events which indicate when the page load finished
    5. Close the browser

    More features to be implemented later.
    """

    PROXY_HOST = '127.0.0.1'
    CHROME_HOST = '127.0.0.1'
    PAGE_LOAD_TIMEOUT = 20

    def __init__(self, uri_opener, http_traffic_queue):
        self.uri_opener = uri_opener
        self.http_traffic_queue = http_traffic_queue

        self.id = rand_alnum(8)
        self.debugging_id = None

        self.proxy = self.start_proxy()
        self.chrome_process = self.start_chrome_process()
        self.chrome_conn = self.connect_to_chrome()
        self.set_chrome_settings()

    def start_proxy(self):
        proxy = LoggingProxy(self.PROXY_HOST,
                             0,
                             self.uri_opener,
                             name='ChromeProxy',
                             queue=self.http_traffic_queue)

        proxy.set_debugging_id(self.debugging_id)

        proxy.start()
        proxy.wait_for_start()

        return proxy

    def get_proxy_address(self):
        return self.PROXY_HOST, self.proxy.get_bind_port()

    def start_chrome_process(self):
        chrome_process = ChromeProcess()

        proxy_host, proxy_port = self.get_proxy_address()
        chrome_process.set_proxy(proxy_host, proxy_port)

        chrome_process.start()
        chrome_process.wait_for_start()

        return chrome_process

    def connect_to_chrome(self):
        port = self.chrome_process.get_devtools_port()

        # The timeout we specify here is the websocket timeout, which is used
        # for send() and recv() calls. When we send a command wait_result() is
        # called, the websocket timeout might be exceeded multiple times while
        # waiting for the result.
        try:
            chrome_conn = DebugChromeInterface(host=self.CHROME_HOST,
                                               port=port,
                                               timeout=1,
                                               debugging_id=self.debugging_id)
        except ConnectionError:
            msg = 'Failed to connect to Chrome on port %s'
            raise InstrumentedChromeException(msg % port)

        return chrome_conn

    def set_debugging_id(self, debugging_id):
        self.debugging_id = debugging_id
        self.chrome_conn.set_debugging_id(debugging_id)
        self.proxy.set_debugging_id(debugging_id)

    def set_chrome_settings(self):
        """
        Set any configuration settings required for Chrome
        :return: None
        """
        # Disable certificate validation
        self.chrome_conn.Security.setIgnoreCertificateErrors(ignore=True)

        # Enable events
        self.chrome_conn.Page.enable()
        self.chrome_conn.Page.setLifecycleEventsEnabled(enabled=True)

    def load_url(self, url):
        """
        Load an URL into the browser, start listening for events.

        :param url: The URL to load
        :return: This method returns immediately, even if the browser is not
                 able to load the URL and an error was raised.
        """
        url = str(url)
        self.chrome_conn.Page.navigate(url=url,
                                       timeout=self.PAGE_LOAD_TIMEOUT)

    def wait_for_load(self):
        """
        Knowing when a page has completed loading is difficult

        This method will wait for two events:
            * Page.frameStoppedLoading
            * Page.lifecycleEvent with name networkIdle

        If they are not received within PAGE_LOAD_TIMEOUT the method gives up
        and assumes that it is the best thing it can do.

        :return: True when the two events were received
                 False when one or none of the events were received
        """
        events_to_wait_for = [
            {'event': 'Page.frameStoppedLoading',
             'name': None,
             'timeout': self.PAGE_LOAD_TIMEOUT},

            {'event': 'Page.lifecycleEvent',
             'name': 'networkIdle',
             'timeout': self.PAGE_LOAD_TIMEOUT}
        ]

        for event in events_to_wait_for:
            matching_message, messages = self.chrome_conn.wait_event(**event)

            if matching_message is None:
                return False

            msg = 'Received %s from Chrome while waiting for page load (did: %s)'
            args = (event['event'], self.debugging_id)
            om.out.debug(msg % args)

        return True

    def stop(self):
        """
        Stop loading any page and close.

        :return:
        """
        self.chrome_conn.Page.stopLoading()

    def get_dom(self):
        result = self.chrome_conn.Runtime.evaluate(expression='document.body.outerHTML')
        return result['result']['result']['value']

    def terminate(self):
        om.out.debug('Terminating %s (did: %s)' % (self, self.debugging_id))

        try:
            self.proxy.stop()
        except Exception, e:
            msg = 'Failed to stop proxy server, exception: "%s" (did: %s)'
            args = (e, self.debugging_id)
            om.out.debug(msg % args)

        try:
            self.chrome_conn.close()
        except Exception, e:
            msg = 'Failed to close chrome connection, exception: "%s" (did: %s)'
            args = (e, self.debugging_id)
            om.out.debug(msg % args)

        try:
            self.chrome_process.terminate()
        except Exception, e:
            msg = 'Failed to terminate chrome process, exception: "%s" (did: %s)'
            args = (e, self.debugging_id)
            om.out.debug(msg % args)

    def __str__(self):
        proxy_port = self.get_proxy_address()[1]
        devtools_port = self.chrome_process.get_devtools_port()

        pid = self.chrome_process.proc.pid if self.chrome_process.proc is not None else None

        args = (self.id, proxy_port, pid, devtools_port)
        msg = '<InstrumentedChrome (id:%s, proxy:%s, process_id: %s, devtools:%s)>'
        return msg % args


class InstrumentedChromeException(Exception):
    pass
