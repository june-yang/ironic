# Copyright (c) 2017 Hitachi, Ltd.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from oslo_log import log as logging
import pecan
from pecan import rest
from six.moves import http_client
import wsme

from ironic.api.controllers import base
from ironic.api.controllers import link
from ironic.api.controllers.v1 import utils as api_utils
from ironic.api.controllers.v1 import volume_connector
from ironic.api.controllers.v1 import volume_target
from ironic.api import expose
from ironic.common import exception
from ironic.common import policy

LOG = logging.getLogger(__name__)


class Volume(base.APIBase):
    """API representation of a volume root.

    This class exists as a root class for the volume connectors and volume
    targets controllers.
    """

    links = wsme.wsattr([link.Link], readonly=True)
    """A list containing a self link and associated volume links"""

    connectors = wsme.wsattr([link.Link], readonly=True)
    """Links to the volume connectors resource"""

    targets = wsme.wsattr([link.Link], readonly=True)
    """Links to the volume targets resource"""

    @staticmethod
    def convert(node_ident=None):
        url = pecan.request.public_url
        volume = Volume()
        if node_ident:
            resource = 'nodes'
            args = '%s/volume/' % node_ident
        else:
            resource = 'volume'
            args = ''

        volume.links = [
            link.Link.make_link('self', url, resource, args),
            link.Link.make_link('bookmark', url, resource, args,
                                bookmark=True)]

        volume.connectors = [
            link.Link.make_link('self', url, resource, args + 'connectors'),
            link.Link.make_link('bookmark', url, resource, args + 'connectors',
                                bookmark=True)]

        volume.targets = [
            link.Link.make_link('self', url, resource, args + 'targets'),
            link.Link.make_link('bookmark', url, resource, args + 'targets',
                                bookmark=True)]

        return volume


class VolumeController(rest.RestController):
    """REST controller for volume root"""

    _custom_actions = {
        'attach': ['POST'],
        'detach': ['DELETE'],
    }

    _subcontroller_map = {
        'connectors': volume_connector.VolumeConnectorsController,
        'targets': volume_target.VolumeTargetsController
    }

    def __init__(self, node_ident=None):
        super(VolumeController, self).__init__()
        self.parent_node_ident = node_ident

    @expose.expose(Volume)
    def get(self):
        if not api_utils.allow_volume():
            raise exception.NotFound()

        cdict = pecan.request.context.to_policy_values()
        policy.authorize('baremetal:volume:get', cdict, cdict)

        return Volume.convert(self.parent_node_ident)

    @expose.expose(Volume)
    def attach(self, volume_id, connector_uuid, node_id=None):
        cdict = pecan.request.context.to_policy_values()
        policy.authorize('baremetal:volume:attach_volume', cdict, cdict)

        rpc_node = api_utils.get_rpc_node(node_ident)
        topic = pecan.request.rpcapi.get_topic_for(rpc_node)
        return pecan.request.rpcapi.attach_volume(pecan.request.context,
                                                  volume_id,
                                                  connector_uuid,
                                                  node_id,
                                                  topic)

    @expose.expose(Volume)
    def detach(self, volume_id, node_id=None):
        cdict = pecan.request.context.to_policy_values()
        policy.authorize('baremetal:volume:detach_volume', cdict, cdict)

        rpc_node = api_utils.get_rpc_node(node_ident)
        topic = pecan.request.rpcapi.get_topic_for(rpc_node)
        return pecan.request.rpcapi.detach_volume(pecan.request.context,
                                                  volume_id,
                                                  node_id)

    @pecan.expose()
    def _lookup(self, subres, *remainder):
        if not api_utils.allow_volume():
            pecan.abort(http_client.NOT_FOUND)
        subcontroller = self._subcontroller_map.get(subres)
        if subcontroller:
            return subcontroller(node_ident=self.parent_node_ident), remainder
