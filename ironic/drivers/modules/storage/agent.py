# Copyright 2016 Hewlett Packard Enterprise Development Company LP.
# Copyright 2016 IBM Corp
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import retrying
import requests

from oslo_config import cfg
from oslo_log import log
from oslo_utils import excutils

from ironic.drivers.modules.storage.cinder import CinderStorage
from ironic.common import exception
from ironic.common.i18n import _
from ironic.common import cinder
from ironic.drivers import utils
from ironic import objects

CONF = cfg.CONF

LOG = log.getLogger(__name__)


class AgentStorage(CinderStorage):

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({'Content-Type': 'application/json'})

    def _get_agent_url(self, task):
        node_extra_networks = task.node
        agent_url = None
        for network in node_extra_networks:
            if 'MANAGE_NETWORK' in network:
                agent_url = 'http://%s:%s' % (network['IP'], CONF.agent.default_listen_port)

        return ('%(agent_url)s/%(api_version)s/volumes/' %
                {'agent_url': agent_url,
                 'api_version': CONF.agent.agent_api_version})

    @retrying.retry(
        retry_on_exception=lambda e: isinstance(e, exception.ServiceUnavailable),
        stop_max_attempt_number=CONF.agent.retry_max + 1,
        wait_fixed=CONF.agent.retry_interval * 1000)
    def _method_wapper(self, task, method, params=None):
        url = self._get_agent_url(task) + method
        try:
            if params:
                response = self.session.post(url, params=params)
            else:
                response = self.session.get(url)
        except requests.exceptions.ConnectionError:
            raise exception.ServiceUnavailable('Agent not available')
        except requests.RequestException as e:
            msg = (_('Error invoking agent command %(method)s for node '
                     '%(node)s. Error: %(error)s') %
                   {'method': method, 'node': task.node.uuid, 'error': e})
            LOG.error(msg)
            raise exception.IronicException(msg)
        try:
            result = response.json()
        except ValueError:
            msg = _(
                'Unable to decode response as JSON.\n'
                'Request URL: %(url)s\nRequest body: "%(body)s"\n'
                'Response status code: %(code)s\n'
                'Response: "%(response)s"'
            ) % ({'response': response.text, 'body': params, 'url': url,
                  'code': response.status_code})
            LOG.error(msg)
            raise exception.IronicException(msg)

        LOG.debug('Agent command %(method)s for node %(node)s returned '
                  'result %(res)s, error %(error)s, HTTP status code %(code)d',
                  {'node': task.node.uuid, 'method': method,
                   'res': result.get('command_result'),
                   'error': result.get('command_error'),
                   'code': response.status_code})
        return result
        

    def get_volume_connector(self, task):
        conncetor = None
        try:
            conncetor = self._method_wapper(task, 'get_volume_connector')
        except exception.ServiceUnavailable:
            LOG.error(_('Can not dispatch volume manage agent service'))

        return conncetor


    def attach_data_volume(self, task, volume_id, connection_info):
        self._method_wapper(task,
                            'connect_volume',
                            {'volume_id': volume_id, 'data': connection_info})

    def detach_data_volume(self, task, volume_id, connection_info):
        self._method_wapper(task,
                            'disconnect_volume',
                            {'volume_id': volume_id, 'data': connection_info})
