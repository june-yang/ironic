# -*- encoding: utf-8 -*-
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
"""
Tests for the API /ports/ methods.
"""

import datetime

import mock
from oslo_config import cfg
from oslo_utils import timeutils
from oslo_utils import uuidutils
import six
from six.moves import http_client
from six.moves.urllib import parse as urlparse
from testtools import matchers
from wsme import types as wtypes

from ironic.api.controllers import base as api_base
from ironic.api.controllers import v1 as api_v1
from ironic.api.controllers.v1 import notification_utils
from ironic.api.controllers.v1 import port as api_port
from ironic.api.controllers.v1 import utils as api_utils
from ironic.api.controllers.v1 import versions
from ironic.common import exception
from ironic.common import utils as common_utils
from ironic.conductor import rpcapi
from ironic import objects
from ironic.objects import fields as obj_fields
from ironic.tests import base
from ironic.tests.unit.api import base as test_api_base
from ironic.tests.unit.api import utils as apiutils
from ironic.tests.unit.db import utils as dbutils
from ironic.tests.unit.objects import utils as obj_utils


# NOTE(lucasagomes): When creating a port via API (POST)
#                    we have to use node_uuid and portgroup_uuid
def post_get_test_port(**kw):
    port = apiutils.port_post_data(**kw)
    node = dbutils.get_test_node()
    portgroup = dbutils.get_test_portgroup()
    port['node_uuid'] = kw.get('node_uuid', node['uuid'])
    port['portgroup_uuid'] = kw.get('portgroup_uuid', portgroup['uuid'])
    return port


class TestPortObject(base.TestCase):

    @mock.patch("pecan.request")
    def test_port_init(self, mock_pecan_req):
        mock_pecan_req.version.minor = 1
        port_dict = apiutils.port_post_data(node_id=None,
                                            portgroup_uuid=None)
        del port_dict['extra']
        port = api_port.Port(**port_dict)
        self.assertEqual(wtypes.Unset, port.extra)


class TestListPorts(test_api_base.BaseApiTest):

    def setUp(self):
        super(TestListPorts, self).setUp()
        self.node = obj_utils.create_test_node(self.context)

    def test_empty(self):
        data = self.get_json('/ports')
        self.assertEqual([], data['ports'])

    def test_one(self):
        port = obj_utils.create_test_port(self.context, node_id=self.node.id)
        data = self.get_json('/ports')
        self.assertEqual(port.uuid, data['ports'][0]["uuid"])
        self.assertNotIn('extra', data['ports'][0])
        self.assertNotIn('node_uuid', data['ports'][0])
        # never expose the node_id
        self.assertNotIn('node_id', data['ports'][0])

    def test_get_one(self):
        port = obj_utils.create_test_port(self.context, node_id=self.node.id)
        data = self.get_json('/ports/%s' % port.uuid)
        self.assertEqual(port.uuid, data['uuid'])
        self.assertIn('extra', data)
        self.assertIn('node_uuid', data)
        # never expose the node_id, port_id, portgroup_id
        self.assertNotIn('node_id', data)
        self.assertNotIn('port_id', data)
        self.assertNotIn('portgroup_id', data)
        self.assertNotIn('portgroup_uuid', data)

    def test_get_one_portgroup_is_none(self):
        port = obj_utils.create_test_port(self.context, node_id=self.node.id)
        data = self.get_json('/ports/%s' % port.uuid,
                             headers={api_base.Version.string: '1.24'})
        self.assertEqual(port.uuid, data['uuid'])
        self.assertIn('extra', data)
        self.assertIn('node_uuid', data)
        # never expose the node_id, port_id, portgroup_id
        self.assertNotIn('node_id', data)
        self.assertNotIn('port_id', data)
        self.assertNotIn('portgroup_id', data)
        self.assertIn('portgroup_uuid', data)

    def test_get_one_custom_fields(self):
        port = obj_utils.create_test_port(self.context, node_id=self.node.id)
        fields = 'address,extra'
        data = self.get_json(
            '/ports/%s?fields=%s' % (port.uuid, fields),
            headers={api_base.Version.string: str(api_v1.MAX_VER)})
        # We always append "links"
        self.assertItemsEqual(['address', 'extra', 'links'], data)

    def test_hide_fields_in_newer_versions_internal_info(self):
        port = obj_utils.create_test_port(self.context, node_id=self.node.id,
                                          internal_info={"foo": "bar"})
        data = self.get_json(
            '/ports/%s' % port.uuid,
            headers={api_base.Version.string: str(api_v1.MIN_VER)})
        self.assertNotIn('internal_info', data)

        data = self.get_json('/ports/%s' % port.uuid,
                             headers={api_base.Version.string: "1.18"})
        self.assertEqual({"foo": "bar"}, data['internal_info'])

    def test_get_collection_custom_fields(self):
        fields = 'uuid,extra'
        for i in range(3):
            obj_utils.create_test_port(self.context,
                                       node_id=self.node.id,
                                       uuid=uuidutils.generate_uuid(),
                                       address='52:54:00:cf:2d:3%s' % i)

        data = self.get_json(
            '/ports?fields=%s' % fields,
            headers={api_base.Version.string: str(api_v1.MAX_VER)})

        self.assertEqual(3, len(data['ports']))
        for port in data['ports']:
            # We always append "links"
            self.assertItemsEqual(['uuid', 'extra', 'links'], port)

    def test_get_custom_fields_invalid_fields(self):
        port = obj_utils.create_test_port(self.context, node_id=self.node.id)
        fields = 'uuid,spongebob'
        response = self.get_json(
            '/ports/%s?fields=%s' % (port.uuid, fields),
            headers={api_base.Version.string: str(api_v1.MAX_VER)},
            expect_errors=True)
        self.assertEqual(http_client.BAD_REQUEST, response.status_int)
        self.assertEqual('application/json', response.content_type)
        self.assertIn('spongebob', response.json['error_message'])

    def test_get_custom_fields_invalid_api_version(self):
        port = obj_utils.create_test_port(self.context, node_id=self.node.id)
        fields = 'uuid,extra'
        response = self.get_json(
            '/ports/%s?fields=%s' % (port.uuid, fields),
            headers={api_base.Version.string: str(api_v1.MIN_VER)},
            expect_errors=True)
        self.assertEqual(http_client.NOT_ACCEPTABLE, response.status_int)

    def test_detail(self):
        llc = {'switch_info': 'switch', 'switch_id': 'aa:bb:cc:dd:ee:ff',
               'port_id': 'Gig0/1'}
        portgroup = obj_utils.create_test_portgroup(self.context,
                                                    node_id=self.node.id)
        port = obj_utils.create_test_port(self.context, node_id=self.node.id,
                                          portgroup_id=portgroup.id,
                                          pxe_enabled=False,
                                          local_link_connection=llc)
        data = self.get_json(
            '/ports/detail',
            headers={api_base.Version.string: str(api_v1.MAX_VER)}
        )
        self.assertEqual(port.uuid, data['ports'][0]["uuid"])
        self.assertIn('extra', data['ports'][0])
        self.assertIn('internal_info', data['ports'][0])
        self.assertIn('node_uuid', data['ports'][0])
        self.assertIn('pxe_enabled', data['ports'][0])
        self.assertIn('local_link_connection', data['ports'][0])
        self.assertIn('portgroup_uuid', data['ports'][0])
        # never expose the node_id and portgroup_id
        self.assertNotIn('node_id', data['ports'][0])
        self.assertNotIn('portgroup_id', data['ports'][0])

    def test_detail_against_single(self):
        port = obj_utils.create_test_port(self.context, node_id=self.node.id)
        response = self.get_json('/ports/%s/detail' % port.uuid,
                                 expect_errors=True)
        self.assertEqual(http_client.NOT_FOUND, response.status_int)

    def test_many(self):
        ports = []
        for id_ in range(5):
            port = obj_utils.create_test_port(
                self.context, node_id=self.node.id,
                uuid=uuidutils.generate_uuid(),
                address='52:54:00:cf:2d:3%s' % id_)
            ports.append(port.uuid)
        data = self.get_json('/ports')
        self.assertEqual(len(ports), len(data['ports']))

        uuids = [n['uuid'] for n in data['ports']]
        six.assertCountEqual(self, ports, uuids)

    def _test_links(self, public_url=None):
        cfg.CONF.set_override('public_endpoint', public_url, 'api')
        uuid = uuidutils.generate_uuid()
        obj_utils.create_test_port(self.context,
                                   uuid=uuid,
                                   node_id=self.node.id)
        data = self.get_json('/ports/%s' % uuid)
        self.assertIn('links', data.keys())
        self.assertEqual(2, len(data['links']))
        self.assertIn(uuid, data['links'][0]['href'])
        for l in data['links']:
            bookmark = l['rel'] == 'bookmark'
            self.assertTrue(self.validate_link(l['href'], bookmark=bookmark))

        if public_url is not None:
            expected = [{'href': '%s/v1/ports/%s' % (public_url, uuid),
                         'rel': 'self'},
                        {'href': '%s/ports/%s' % (public_url, uuid),
                         'rel': 'bookmark'}]
            for i in expected:
                self.assertIn(i, data['links'])

    def test_links(self):
        self._test_links()

    def test_links_public_url(self):
        self._test_links(public_url='http://foo')

    def test_collection_links(self):
        ports = []
        for id_ in range(5):
            port = obj_utils.create_test_port(
                self.context,
                node_id=self.node.id,
                uuid=uuidutils.generate_uuid(),
                address='52:54:00:cf:2d:3%s' % id_)
            ports.append(port.uuid)
        data = self.get_json('/ports/?limit=3')
        self.assertEqual(3, len(data['ports']))

        next_marker = data['ports'][-1]['uuid']
        self.assertIn(next_marker, data['next'])

    def test_collection_links_default_limit(self):
        cfg.CONF.set_override('max_limit', 3, 'api')
        ports = []
        for id_ in range(5):
            port = obj_utils.create_test_port(
                self.context,
                node_id=self.node.id,
                uuid=uuidutils.generate_uuid(),
                address='52:54:00:cf:2d:3%s' % id_)
            ports.append(port.uuid)
        data = self.get_json('/ports')
        self.assertEqual(3, len(data['ports']))

        next_marker = data['ports'][-1]['uuid']
        self.assertIn(next_marker, data['next'])

    def test_port_by_address(self):
        address_template = "aa:bb:cc:dd:ee:f%d"
        for id_ in range(3):
            obj_utils.create_test_port(self.context,
                                       node_id=self.node.id,
                                       uuid=uuidutils.generate_uuid(),
                                       address=address_template % id_)

        target_address = address_template % 1
        data = self.get_json('/ports?address=%s' % target_address)
        self.assertThat(data['ports'], matchers.HasLength(1))
        self.assertEqual(target_address, data['ports'][0]['address'])

    def test_port_by_address_non_existent_address(self):
        # non-existent address
        data = self.get_json('/ports?address=%s' % 'aa:bb:cc:dd:ee:ff')
        self.assertThat(data['ports'], matchers.HasLength(0))

    def test_port_by_address_invalid_address_format(self):
        obj_utils.create_test_port(self.context, node_id=self.node.id)
        invalid_address = 'invalid-mac-format'
        response = self.get_json('/ports?address=%s' % invalid_address,
                                 expect_errors=True)
        self.assertEqual(http_client.BAD_REQUEST, response.status_int)
        self.assertEqual('application/json', response.content_type)
        self.assertIn(invalid_address, response.json['error_message'])

    def test_sort_key(self):
        ports = []
        for id_ in range(3):
            port = obj_utils.create_test_port(
                self.context,
                node_id=self.node.id,
                uuid=uuidutils.generate_uuid(),
                address='52:54:00:cf:2d:3%s' % id_)
            ports.append(port.uuid)
        data = self.get_json('/ports?sort_key=uuid')
        uuids = [n['uuid'] for n in data['ports']]
        self.assertEqual(sorted(ports), uuids)

    def test_sort_key_invalid(self):
        invalid_keys_list = ['foo', 'extra', 'internal_info',
                             'local_link_connection']
        for invalid_key in invalid_keys_list:
            response = self.get_json(
                '/ports?sort_key=%s' % invalid_key, expect_errors=True,
                headers={api_base.Version.string: str(api_v1.MAX_VER)}
            )
            self.assertEqual(http_client.BAD_REQUEST, response.status_int)
            self.assertEqual('application/json', response.content_type)
            self.assertIn(invalid_key, response.json['error_message'])

    @mock.patch.object(api_utils, 'get_rpc_node')
    def test_get_all_by_node_name_ok(self, mock_get_rpc_node):
        # GET /v1/ports specifying node_name - success
        mock_get_rpc_node.return_value = self.node
        for i in range(5):
            if i < 3:
                node_id = self.node.id
            else:
                node_id = 100000 + i
            obj_utils.create_test_port(self.context,
                                       node_id=node_id,
                                       uuid=uuidutils.generate_uuid(),
                                       address='52:54:00:cf:2d:3%s' % i)
        data = self.get_json("/ports?node=%s" % 'test-node',
                             headers={api_base.Version.string: '1.5'})
        self.assertEqual(3, len(data['ports']))

    @mock.patch.object(api_utils, 'get_rpc_node')
    def test_get_all_by_node_uuid_and_name(self, mock_get_rpc_node):
        # GET /v1/ports specifying node and uuid - should only use node_uuid
        mock_get_rpc_node.return_value = self.node
        obj_utils.create_test_port(self.context, node_id=self.node.id)
        self.get_json('/ports/detail?node_uuid=%s&node=%s' %
                      (self.node.uuid, 'node-name'))
        mock_get_rpc_node.assert_called_once_with(self.node.uuid)

    @mock.patch.object(api_utils, 'get_rpc_node')
    def test_get_all_by_node_name_not_supported(self, mock_get_rpc_node):
        # GET /v1/ports specifying node_name - name not supported
        mock_get_rpc_node.side_effect = (
            exception.InvalidUuidOrName(name=self.node.uuid))
        for i in range(3):
            obj_utils.create_test_port(self.context,
                                       node_id=self.node.id,
                                       uuid=uuidutils.generate_uuid(),
                                       address='52:54:00:cf:2d:3%s' % i)
        data = self.get_json("/ports?node=%s" % 'test-node',
                             expect_errors=True)
        self.assertEqual(0, mock_get_rpc_node.call_count)
        self.assertEqual(http_client.NOT_ACCEPTABLE, data.status_int)

    @mock.patch.object(api_utils, 'get_rpc_node')
    def test_detail_by_node_name_ok(self, mock_get_rpc_node):
        # GET /v1/ports/detail specifying node_name - success
        mock_get_rpc_node.return_value = self.node
        port = obj_utils.create_test_port(self.context, node_id=self.node.id)
        data = self.get_json('/ports/detail?node=%s' % 'test-node',
                             headers={api_base.Version.string: '1.5'})
        self.assertEqual(port.uuid, data['ports'][0]['uuid'])
        self.assertEqual(self.node.uuid, data['ports'][0]['node_uuid'])

    @mock.patch.object(api_utils, 'get_rpc_node')
    def test_detail_by_node_name_not_supported(self, mock_get_rpc_node):
        # GET /v1/ports/detail specifying node_name - name not supported
        mock_get_rpc_node.side_effect = (
            exception.InvalidUuidOrName(name=self.node.uuid))
        obj_utils.create_test_port(self.context, node_id=self.node.id)
        data = self.get_json('/ports/detail?node=%s' % 'test-node',
                             expect_errors=True)
        self.assertEqual(0, mock_get_rpc_node.call_count)
        self.assertEqual(http_client.NOT_ACCEPTABLE, data.status_int)

    def test_get_all_by_portgroup_uuid(self):
        pg = obj_utils.create_test_portgroup(self.context,
                                             node_id=self.node.id)
        port = obj_utils.create_test_port(self.context, node_id=self.node.id,
                                          portgroup_id=pg.id)
        data = self.get_json('/ports/detail?portgroup=%s' % pg.uuid,
                             headers={api_base.Version.string: '1.24'})
        self.assertEqual(port.uuid, data['ports'][0]['uuid'])
        self.assertEqual(pg.uuid,
                         data['ports'][0]['portgroup_uuid'])

    def test_get_all_by_portgroup_uuid_older_api_version(self):
        pg = obj_utils.create_test_portgroup(self.context,
                                             node_id=self.node.id)
        response = self.get_json(
            '/ports/detail?portgroup=%s' % pg.uuid,
            headers={api_base.Version.string: '1.14'},
            expect_errors=True
        )
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.NOT_ACCEPTABLE, response.status_int)

    def test_get_all_by_portgroup_name(self):
        pg = obj_utils.create_test_portgroup(self.context,
                                             node_id=self.node.id)
        port = obj_utils.create_test_port(self.context, node_id=self.node.id,
                                          portgroup_id=pg.id)
        data = self.get_json('/ports/detail?portgroup=%s' % pg.name,
                             headers={api_base.Version.string: '1.24'})
        self.assertEqual(port.uuid, data['ports'][0]['uuid'])
        self.assertEqual(pg.uuid,
                         data['ports'][0]['portgroup_uuid'])
        self.assertEqual(1, len(data['ports']))

    def test_get_all_by_portgroup_uuid_and_node_uuid(self):
        pg = obj_utils.create_test_portgroup(self.context,
                                             node_id=self.node.id)
        response = self.get_json(
            '/ports/detail?portgroup=%s&node=%s' % (pg.uuid, self.node.uuid),
            headers={api_base.Version.string: '1.24'},
            expect_errors=True)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.FORBIDDEN, response.status_int)

    @mock.patch.object(api_port.PortsController, '_get_ports_collection')
    def test_detail_with_incorrect_api_usage(self, mock_gpc):
        # GET /v1/ports/detail specifying node and node_uuid.  In this case
        # we expect the node_uuid interface to be used.
        self.get_json('/ports/detail?node=%s&node_uuid=%s' %
                      ('test-node', self.node.uuid))
        mock_gpc.assert_called_once_with(self.node.uuid, mock.ANY, mock.ANY,
                                         mock.ANY, mock.ANY, mock.ANY,
                                         mock.ANY, mock.ANY)

    def test_portgroups_subresource_node_not_found(self):
        non_existent_uuid = 'eeeeeeee-cccc-aaaa-bbbb-cccccccccccc'
        response = self.get_json('/portgroups/%s/ports' % non_existent_uuid,
                                 expect_errors=True)
        self.assertEqual(http_client.NOT_FOUND, response.status_int)

    def test_portgroups_subresource_invalid_ident(self):
        invalid_ident = '123 123'
        response = self.get_json('/portgroups/%s/ports' % invalid_ident,
                                 headers={api_base.Version.string: '1.24'},
                                 expect_errors=True)
        self.assertEqual(http_client.BAD_REQUEST, response.status_int)
        self.assertIn('Expected a logical name or UUID',
                      response.json['error_message'])


@mock.patch.object(rpcapi.ConductorAPI, 'update_port')
class TestPatch(test_api_base.BaseApiTest):

    def setUp(self):
        super(TestPatch, self).setUp()
        self.node = obj_utils.create_test_node(self.context)
        self.port = obj_utils.create_test_port(self.context,
                                               node_id=self.node.id)

        p = mock.patch.object(rpcapi.ConductorAPI, 'get_topic_for')
        self.mock_gtf = p.start()
        self.mock_gtf.return_value = 'test-topic'
        self.addCleanup(p.stop)

    @mock.patch.object(notification_utils, '_emit_api_notification')
    def test_update_byid(self, mock_notify, mock_upd):
        extra = {'foo': 'bar'}
        mock_upd.return_value = self.port
        mock_upd.return_value.extra = extra
        response = self.patch_json('/ports/%s' % self.port.uuid,
                                   [{'path': '/extra/foo',
                                     'value': 'bar',
                                     'op': 'add'}])
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.OK, response.status_code)
        self.assertEqual(extra, response.json['extra'])

        kargs = mock_upd.call_args[0][1]
        self.assertEqual(extra, kargs.extra)
        mock_notify.assert_has_calls([mock.call(mock.ANY, mock.ANY, 'update',
                                      obj_fields.NotificationLevel.INFO,
                                      obj_fields.NotificationStatus.START,
                                      node_uuid=self.node.uuid),
                                      mock.call(mock.ANY, mock.ANY, 'update',
                                      obj_fields.NotificationLevel.INFO,
                                      obj_fields.NotificationStatus.END,
                                      node_uuid=self.node.uuid)])

    def test_update_byaddress_not_allowed(self, mock_upd):
        extra = {'foo': 'bar'}
        mock_upd.return_value = self.port
        mock_upd.return_value.extra = extra
        response = self.patch_json('/ports/%s' % self.port.address,
                                   [{'path': '/extra/foo',
                                     'value': 'bar',
                                     'op': 'add'}],
                                   expect_errors=True)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.BAD_REQUEST, response.status_int)
        self.assertIn(self.port.address, response.json['error_message'])
        self.assertFalse(mock_upd.called)

    def test_update_not_found(self, mock_upd):
        uuid = uuidutils.generate_uuid()
        response = self.patch_json('/ports/%s' % uuid,
                                   [{'path': '/extra/foo',
                                     'value': 'bar',
                                     'op': 'add'}],
                                   expect_errors=True)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.NOT_FOUND, response.status_int)
        self.assertTrue(response.json['error_message'])
        self.assertFalse(mock_upd.called)

    def test_replace_singular(self, mock_upd):
        address = 'aa:bb:cc:dd:ee:ff'
        mock_upd.return_value = self.port
        mock_upd.return_value.address = address
        response = self.patch_json('/ports/%s' % self.port.uuid,
                                   [{'path': '/address',
                                     'value': address,
                                     'op': 'replace'}])
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.OK, response.status_code)
        self.assertEqual(address, response.json['address'])
        self.assertTrue(mock_upd.called)

        kargs = mock_upd.call_args[0][1]
        self.assertEqual(address, kargs.address)

    @mock.patch.object(notification_utils, '_emit_api_notification')
    def test_replace_address_already_exist(self, mock_notify, mock_upd):
        address = 'aa:aa:aa:aa:aa:aa'
        mock_upd.side_effect = exception.MACAlreadyExists(mac=address)
        response = self.patch_json('/ports/%s' % self.port.uuid,
                                   [{'path': '/address',
                                     'value': address,
                                     'op': 'replace'}],
                                   expect_errors=True)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.CONFLICT, response.status_code)
        self.assertTrue(response.json['error_message'])
        self.assertTrue(mock_upd.called)

        kargs = mock_upd.call_args[0][1]
        self.assertEqual(address, kargs.address)
        mock_notify.assert_has_calls([mock.call(mock.ANY, mock.ANY, 'update',
                                      obj_fields.NotificationLevel.INFO,
                                      obj_fields.NotificationStatus.START,
                                      node_uuid=self.node.uuid),
                                      mock.call(mock.ANY, mock.ANY, 'update',
                                      obj_fields.NotificationLevel.ERROR,
                                      obj_fields.NotificationStatus.ERROR,
                                      node_uuid=self.node.uuid)])

    def test_replace_node_uuid(self, mock_upd):
        mock_upd.return_value = self.port
        response = self.patch_json('/ports/%s' % self.port.uuid,
                                   [{'path': '/node_uuid',
                                     'value': self.node.uuid,
                                     'op': 'replace'}])
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.OK, response.status_code)

    def test_replace_local_link_connection(self, mock_upd):
        switch_id = 'aa:bb:cc:dd:ee:ff'
        mock_upd.return_value = self.port
        mock_upd.return_value.local_link_connection['switch_id'] = switch_id
        response = self.patch_json('/ports/%s' % self.port.uuid,
                                   [{'path':
                                     '/local_link_connection/switch_id',
                                     'value': switch_id,
                                     'op': 'replace'}],
                                   headers={api_base.Version.string: '1.19'})
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.OK, response.status_code)
        self.assertEqual(switch_id,
                         response.json['local_link_connection']['switch_id'])
        self.assertTrue(mock_upd.called)

        kargs = mock_upd.call_args[0][1]
        self.assertEqual(switch_id, kargs.local_link_connection['switch_id'])

    def test_remove_local_link_connection_old_api(self, mock_upd):
        response = self.patch_json(
            '/ports/%s' % self.port.uuid,
            [{'path': '/local_link_connection/switch_id', 'op': 'remove'}],
            expect_errors=True)
        self.assertEqual('application/json', response.content_type)
        self.assertTrue(response.json['error_message'])
        self.assertEqual(http_client.NOT_ACCEPTABLE, response.status_code)

    def test_set_pxe_enabled_false_old_api(self, mock_upd):
        response = self.patch_json('/ports/%s' % self.port.uuid,
                                   [{'path': '/pxe_enabled',
                                     'value': False,
                                     'op': 'add'}],
                                   expect_errors=True)
        self.assertEqual('application/json', response.content_type)
        self.assertTrue(response.json['error_message'])
        self.assertEqual(http_client.NOT_ACCEPTABLE, response.status_code)

    def test_add_portgroup_uuid(self, mock_upd):
        mock_upd.return_value = self.port
        pg = obj_utils.create_test_portgroup(self.context,
                                             node_id=self.node.id,
                                             uuid=uuidutils.generate_uuid(),
                                             address='bb:bb:bb:bb:bb:bb',
                                             name='bar')
        headers = {api_base.Version.string: '1.24'}
        response = self.patch_json('/ports/%s' % self.port.uuid,
                                   [{'path':
                                     '/portgroup_uuid',
                                     'value': pg.uuid,
                                     'op': 'add'}],
                                   headers=headers)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.OK, response.status_code)

    def test_replace_portgroup_uuid(self, mock_upd):
        pg = obj_utils.create_test_portgroup(self.context,
                                             node_id=self.node.id,
                                             uuid=uuidutils.generate_uuid(),
                                             address='bb:bb:bb:bb:bb:bb',
                                             name='bar')
        mock_upd.return_value = self.port
        headers = {api_base.Version.string: '1.24'}
        response = self.patch_json('/ports/%s' % self.port.uuid,
                                   [{'path': '/portgroup_uuid',
                                     'value': pg.uuid,
                                     'op': 'replace'}],
                                   headers=headers)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.OK, response.status_code)

    def test_replace_portgroup_uuid_remove(self, mock_upd):
        pg = obj_utils.create_test_portgroup(self.context,
                                             node_id=self.node.id,
                                             uuid=uuidutils.generate_uuid(),
                                             address='bb:bb:bb:bb:bb:bb',
                                             name='bar')
        mock_upd.return_value = self.port
        headers = {api_base.Version.string: '1.24'}
        response = self.patch_json('/ports/%s' % self.port.uuid,
                                   [{'path': '/portgroup_uuid',
                                     'value': pg.uuid,
                                     'op': 'remove'}],
                                   headers=headers)
        self.assertEqual('application/json', response.content_type)
        self.assertIsNone(mock_upd.call_args[0][1].portgroup_id)

    def test_replace_portgroup_uuid_remove_add(self, mock_upd):
        pg = obj_utils.create_test_portgroup(self.context,
                                             node_id=self.node.id,
                                             uuid=uuidutils.generate_uuid(),
                                             address='bb:bb:bb:bb:bb:bb',
                                             name='bar')
        pg1 = obj_utils.create_test_portgroup(self.context,
                                              node_id=self.node.id,
                                              uuid=uuidutils.generate_uuid(),
                                              address='bb:bb:bb:bb:bb:b1',
                                              name='bbb')
        mock_upd.return_value = self.port
        headers = {api_base.Version.string: '1.24'}
        response = self.patch_json('/ports/%s' % self.port.uuid,
                                   [{'path': '/portgroup_uuid',
                                     'value': pg.uuid,
                                     'op': 'remove'},
                                    {'path': '/portgroup_uuid',
                                     'value': pg1.uuid,
                                     'op': 'add'}],
                                   headers=headers)
        self.assertEqual('application/json', response.content_type)
        self.assertTrue(pg1.id, mock_upd.call_args[0][1].portgroup_id)

    def test_replace_portgroup_uuid_old_api(self, mock_upd):
        pg = obj_utils.create_test_portgroup(self.context,
                                             node_id=self.node.id,
                                             uuid=uuidutils.generate_uuid(),
                                             address='bb:bb:bb:bb:bb:bb',
                                             name='bar')
        mock_upd.return_value = self.port
        headers = {api_base.Version.string: '1.15'}
        response = self.patch_json('/ports/%s' % self.port.uuid,
                                   [{'path': '/portgroup_uuid',
                                     'value': pg.uuid,
                                     'op': 'replace'}],
                                   headers=headers,
                                   expect_errors=True)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.NOT_ACCEPTABLE, response.status_code)

    def test_add_node_uuid(self, mock_upd):
        mock_upd.return_value = self.port
        response = self.patch_json('/ports/%s' % self.port.uuid,
                                   [{'path': '/node_uuid',
                                     'value': self.node.uuid,
                                     'op': 'add'}])
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.OK, response.status_code)

    def test_add_node_id(self, mock_upd):
        response = self.patch_json('/ports/%s' % self.port.uuid,
                                   [{'path': '/node_id',
                                     'value': '1',
                                     'op': 'add'}],
                                   expect_errors=True)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.BAD_REQUEST, response.status_code)
        self.assertFalse(mock_upd.called)

    def test_replace_node_id(self, mock_upd):
        response = self.patch_json('/ports/%s' % self.port.uuid,
                                   [{'path': '/node_id',
                                     'value': '1',
                                     'op': 'replace'}],
                                   expect_errors=True)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.BAD_REQUEST, response.status_code)
        self.assertFalse(mock_upd.called)

    def test_remove_node_id(self, mock_upd):
        response = self.patch_json('/ports/%s' % self.port.uuid,
                                   [{'path': '/node_id',
                                     'op': 'remove'}],
                                   expect_errors=True)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.BAD_REQUEST, response.status_code)
        self.assertFalse(mock_upd.called)

    def test_replace_non_existent_node_uuid(self, mock_upd):
        node_uuid = '12506333-a81c-4d59-9987-889ed5f8687b'
        response = self.patch_json('/ports/%s' % self.port.uuid,
                                   [{'path': '/node_uuid',
                                     'value': node_uuid,
                                     'op': 'replace'}],
                                   expect_errors=True)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.BAD_REQUEST, response.status_code)
        self.assertIn(node_uuid, response.json['error_message'])
        self.assertFalse(mock_upd.called)

    def test_replace_multi(self, mock_upd):
        extra = {"foo1": "bar1", "foo2": "bar2", "foo3": "bar3"}
        self.port.extra = extra
        self.port.save()

        # mutate extra so we replace all of them
        extra = dict((k, extra[k] + 'x') for k in extra.keys())

        patch = []
        for k in extra.keys():
            patch.append({'path': '/extra/%s' % k,
                          'value': extra[k],
                          'op': 'replace'})
        mock_upd.return_value = self.port
        mock_upd.return_value.extra = extra
        response = self.patch_json('/ports/%s' % self.port.uuid,
                                   patch)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.OK, response.status_code)
        self.assertEqual(extra, response.json['extra'])
        kargs = mock_upd.call_args[0][1]
        self.assertEqual(extra, kargs.extra)

    def test_remove_multi(self, mock_upd):
        extra = {"foo1": "bar1", "foo2": "bar2", "foo3": "bar3"}
        self.port.extra = extra
        self.port.save()

        # Removing one item from the collection
        extra.pop('foo1')
        mock_upd.return_value = self.port
        mock_upd.return_value.extra = extra
        response = self.patch_json('/ports/%s' % self.port.uuid,
                                   [{'path': '/extra/foo1',
                                     'op': 'remove'}])
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.OK, response.status_code)
        self.assertEqual(extra, response.json['extra'])
        kargs = mock_upd.call_args[0][1]
        self.assertEqual(extra, kargs.extra)

        # Removing the collection
        extra = {}
        mock_upd.return_value.extra = extra
        response = self.patch_json('/ports/%s' % self.port.uuid,
                                   [{'path': '/extra', 'op': 'remove'}])
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.OK, response.status_code)
        self.assertEqual({}, response.json['extra'])
        kargs = mock_upd.call_args[0][1]
        self.assertEqual(extra, kargs.extra)

        # Assert nothing else was changed
        self.assertEqual(self.port.uuid, response.json['uuid'])
        self.assertEqual(self.port.address, response.json['address'])

    def test_remove_non_existent_property_fail(self, mock_upd):
        response = self.patch_json('/ports/%s' % self.port.uuid,
                                   [{'path': '/extra/non-existent',
                                     'op': 'remove'}],
                                   expect_errors=True)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.BAD_REQUEST, response.status_code)
        self.assertTrue(response.json['error_message'])
        self.assertFalse(mock_upd.called)

    def test_remove_mandatory_field(self, mock_upd):
        response = self.patch_json('/ports/%s' % self.port.uuid,
                                   [{'path': '/address',
                                     'op': 'remove'}],
                                   expect_errors=True)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.BAD_REQUEST, response.status_code)
        self.assertTrue(response.json['error_message'])
        self.assertIn('mandatory attribute', response.json['error_message'])
        self.assertFalse(mock_upd.called)

    def test_add_root(self, mock_upd):
        address = 'aa:bb:cc:dd:ee:ff'
        mock_upd.return_value = self.port
        mock_upd.return_value.address = address
        response = self.patch_json('/ports/%s' % self.port.uuid,
                                   [{'path': '/address',
                                     'value': address,
                                     'op': 'add'}])
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.OK, response.status_code)
        self.assertEqual(address, response.json['address'])
        self.assertTrue(mock_upd.called)
        kargs = mock_upd.call_args[0][1]
        self.assertEqual(address, kargs.address)

    def test_add_root_non_existent(self, mock_upd):
        response = self.patch_json('/ports/%s' % self.port.uuid,
                                   [{'path': '/foo',
                                     'value': 'bar',
                                     'op': 'add'}],
                                   expect_errors=True)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.BAD_REQUEST, response.status_int)
        self.assertTrue(response.json['error_message'])
        self.assertFalse(mock_upd.called)

    def test_add_multi(self, mock_upd):
        extra = {"foo1": "bar1", "foo2": "bar2", "foo3": "bar3"}
        patch = []
        for k in extra.keys():
            patch.append({'path': '/extra/%s' % k,
                          'value': extra[k],
                          'op': 'add'})
        mock_upd.return_value = self.port
        mock_upd.return_value.extra = extra
        response = self.patch_json('/ports/%s' % self.port.uuid,
                                   patch)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.OK, response.status_code)
        self.assertEqual(extra, response.json['extra'])
        kargs = mock_upd.call_args[0][1]
        self.assertEqual(extra, kargs.extra)

    def test_remove_uuid(self, mock_upd):
        response = self.patch_json('/ports/%s' % self.port.uuid,
                                   [{'path': '/uuid',
                                     'op': 'remove'}],
                                   expect_errors=True)
        self.assertEqual(http_client.BAD_REQUEST, response.status_int)
        self.assertEqual('application/json', response.content_type)
        self.assertTrue(response.json['error_message'])
        self.assertFalse(mock_upd.called)

    def test_update_address_invalid_format(self, mock_upd):
        response = self.patch_json('/ports/%s' % self.port.uuid,
                                   [{'path': '/address',
                                     'value': 'invalid-format',
                                     'op': 'replace'}],
                                   expect_errors=True)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.BAD_REQUEST, response.status_int)
        self.assertTrue(response.json['error_message'])
        self.assertFalse(mock_upd.called)

    def test_update_port_address_normalized(self, mock_upd):
        address = 'AA:BB:CC:DD:EE:FF'
        mock_upd.return_value = self.port
        mock_upd.return_value.address = address.lower()
        response = self.patch_json('/ports/%s' % self.port.uuid,
                                   [{'path': '/address',
                                     'value': address,
                                     'op': 'replace'}])
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.OK, response.status_code)
        self.assertEqual(address.lower(), response.json['address'])
        kargs = mock_upd.call_args[0][1]
        self.assertEqual(address.lower(), kargs.address)

    def test_update_pxe_enabled_allowed(self, mock_upd):
        pxe_enabled = True
        mock_upd.return_value = self.port
        mock_upd.return_value.pxe_enabled = pxe_enabled
        response = self.patch_json('/ports/%s' % self.port.uuid,
                                   [{'path': '/pxe_enabled',
                                     'value': pxe_enabled,
                                     'op': 'replace'}],
                                   headers={api_base.Version.string: '1.19'})
        self.assertEqual(http_client.OK, response.status_code)
        self.assertEqual(pxe_enabled, response.json['pxe_enabled'])

    def test_update_pxe_enabled_old_api_version(self, mock_upd):
        pxe_enabled = True
        mock_upd.return_value = self.port
        headers = {api_base.Version.string: '1.14'}
        response = self.patch_json('/ports/%s' % self.port.uuid,
                                   [{'path': '/pxe_enabled',
                                     'value': pxe_enabled,
                                     'op': 'replace'}],
                                   expect_errors=True,
                                   headers=headers)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.NOT_ACCEPTABLE, response.status_int)
        self.assertFalse(mock_upd.called)

    def test_portgroups_subresource_patch(self, mock_upd):
        portgroup = obj_utils.create_test_portgroup(self.context,
                                                    node_id=self.node.id)
        port = obj_utils.create_test_port(self.context, node_id=self.node.id,
                                          uuid=uuidutils.generate_uuid(),
                                          portgroup_id=portgroup.id,
                                          address='52:55:00:cf:2d:31')
        headers = {api_base.Version.string: '1.24'}
        response = self.patch_json(
            '/portgroups/%(portgroup)s/ports/%(port)s' %
            {'portgroup': portgroup.uuid, 'port': port.uuid},
            [{'path': '/address', 'value': '00:00:00:00:00:00',
              'op': 'replace'}], headers=headers, expect_errors=True)
        self.assertEqual(http_client.FORBIDDEN, response.status_int)
        self.assertEqual('application/json', response.content_type)


class TestPost(test_api_base.BaseApiTest):

    def setUp(self):
        super(TestPost, self).setUp()
        self.node = obj_utils.create_test_node(self.context)
        self.portgroup = obj_utils.create_test_portgroup(self.context,
                                                         node_id=self.node.id)
        self.headers = {api_base.Version.string: str(
            versions.MAX_VERSION_STRING)}

    @mock.patch.object(common_utils, 'warn_about_deprecated_extra_vif_port_id',
                       autospec=True)
    @mock.patch.object(notification_utils, '_emit_api_notification')
    @mock.patch.object(timeutils, 'utcnow')
    def test_create_port(self, mock_utcnow, mock_notify, mock_warn):
        pdict = post_get_test_port()
        test_time = datetime.datetime(2000, 1, 1, 0, 0)
        mock_utcnow.return_value = test_time
        response = self.post_json('/ports', pdict, headers=self.headers)
        self.assertEqual(http_client.CREATED, response.status_int)
        result = self.get_json('/ports/%s' % pdict['uuid'],
                               headers=self.headers)
        self.assertEqual(pdict['uuid'], result['uuid'])
        self.assertFalse(result['updated_at'])
        return_created_at = timeutils.parse_isotime(
            result['created_at']).replace(tzinfo=None)
        self.assertEqual(test_time, return_created_at)
        # Check location header
        self.assertIsNotNone(response.location)
        expected_location = '/v1/ports/%s' % pdict['uuid']
        self.assertEqual(urlparse.urlparse(response.location).path,
                         expected_location)
        mock_notify.assert_has_calls([mock.call(mock.ANY, mock.ANY, 'create',
                                      obj_fields.NotificationLevel.INFO,
                                      obj_fields.NotificationStatus.START,
                                      node_uuid=self.node.uuid),
                                      mock.call(mock.ANY, mock.ANY, 'create',
                                      obj_fields.NotificationLevel.INFO,
                                      obj_fields.NotificationStatus.END,
                                      node_uuid=self.node.uuid)])
        self.assertEqual(0, mock_warn.call_count)

    def test_create_port_min_api_version(self):
        pdict = post_get_test_port(
            node_uuid=self.node.uuid)
        pdict.pop('local_link_connection')
        pdict.pop('pxe_enabled')
        pdict.pop('extra')
        headers = {api_base.Version.string: str(api_v1.MIN_VER)}
        response = self.post_json('/ports', pdict, headers=headers)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.CREATED, response.status_int)
        self.assertEqual(self.node.uuid, response.json['node_uuid'])

    def test_create_port_doesnt_contain_id(self):
        with mock.patch.object(self.dbapi, 'create_port',
                               wraps=self.dbapi.create_port) as cp_mock:
            pdict = post_get_test_port(extra={'foo': 123})
            self.post_json('/ports', pdict, headers=self.headers)
            result = self.get_json('/ports/%s' % pdict['uuid'],
                                   headers=self.headers)
            self.assertEqual(pdict['extra'], result['extra'])
            cp_mock.assert_called_once_with(mock.ANY)
            # Check that 'id' is not in first arg of positional args
            self.assertNotIn('id', cp_mock.call_args[0][0])

    @mock.patch.object(notification_utils.LOG, 'exception', autospec=True)
    @mock.patch.object(notification_utils.LOG, 'warning', autospec=True)
    def test_create_port_generate_uuid(self, mock_warning, mock_exception):
        pdict = post_get_test_port()
        del pdict['uuid']
        response = self.post_json('/ports', pdict, headers=self.headers)
        result = self.get_json('/ports/%s' % response.json['uuid'],
                               headers=self.headers)
        self.assertEqual(pdict['address'], result['address'])
        self.assertTrue(uuidutils.is_uuid_like(result['uuid']))
        self.assertFalse(mock_warning.called)
        self.assertFalse(mock_exception.called)

    @mock.patch.object(notification_utils, '_emit_api_notification')
    @mock.patch.object(objects.Port, 'create')
    def test_create_port_error(self, mock_create, mock_notify):
        mock_create.side_effect = Exception()
        pdict = post_get_test_port()
        self.post_json('/ports', pdict, headers=self.headers,
                       expect_errors=True)
        mock_notify.assert_has_calls([mock.call(mock.ANY, mock.ANY, 'create',
                                      obj_fields.NotificationLevel.INFO,
                                      obj_fields.NotificationStatus.START,
                                      node_uuid=self.node.uuid),
                                      mock.call(mock.ANY, mock.ANY, 'create',
                                      obj_fields.NotificationLevel.ERROR,
                                      obj_fields.NotificationStatus.ERROR,
                                      node_uuid=self.node.uuid)])

    def test_create_port_valid_extra(self):
        pdict = post_get_test_port(extra={'str': 'foo', 'int': 123,
                                          'float': 0.1, 'bool': True,
                                          'list': [1, 2], 'none': None,
                                          'dict': {'cat': 'meow'}})
        self.post_json('/ports', pdict, headers=self.headers)
        result = self.get_json('/ports/%s' % pdict['uuid'],
                               headers=self.headers)
        self.assertEqual(pdict['extra'], result['extra'])

    def test_create_port_no_mandatory_field_address(self):
        pdict = post_get_test_port()
        del pdict['address']
        response = self.post_json('/ports', pdict, expect_errors=True,
                                  headers=self.headers)
        self.assertEqual(http_client.BAD_REQUEST, response.status_int)
        self.assertEqual('application/json', response.content_type)
        self.assertTrue(response.json['error_message'])

    def test_create_port_no_mandatory_field_node_uuid(self):
        pdict = post_get_test_port()
        del pdict['node_uuid']
        response = self.post_json('/ports', pdict, expect_errors=True)
        self.assertEqual(http_client.BAD_REQUEST, response.status_int)
        self.assertEqual('application/json', response.content_type)
        self.assertTrue(response.json['error_message'])

    def test_create_port_invalid_addr_format(self):
        pdict = post_get_test_port(address='invalid-format')
        response = self.post_json('/ports', pdict, expect_errors=True)
        self.assertEqual(http_client.BAD_REQUEST, response.status_int)
        self.assertEqual('application/json', response.content_type)
        self.assertTrue(response.json['error_message'])

    def test_create_port_address_normalized(self):
        address = 'AA:BB:CC:DD:EE:FF'
        pdict = post_get_test_port(address=address)
        self.post_json('/ports', pdict, headers=self.headers)
        result = self.get_json('/ports/%s' % pdict['uuid'],
                               headers=self.headers)
        self.assertEqual(address.lower(), result['address'])

    def test_create_port_with_hyphens_delimiter(self):
        pdict = post_get_test_port()
        colonsMAC = pdict['address']
        hyphensMAC = colonsMAC.replace(':', '-')
        pdict['address'] = hyphensMAC
        response = self.post_json('/ports', pdict, expect_errors=True)
        self.assertEqual(http_client.BAD_REQUEST, response.status_int)
        self.assertEqual('application/json', response.content_type)
        self.assertTrue(response.json['error_message'])

    def test_create_port_invalid_node_uuid_format(self):
        pdict = post_get_test_port(node_uuid='invalid-format')
        response = self.post_json('/ports', pdict, expect_errors=True)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.BAD_REQUEST, response.status_int)
        self.assertTrue(response.json['error_message'])

    def test_node_uuid_to_node_id_mapping(self):
        pdict = post_get_test_port(node_uuid=self.node['uuid'])
        self.post_json('/ports', pdict, headers=self.headers)
        # GET doesn't return the node_id it's an internal value
        port = self.dbapi.get_port_by_uuid(pdict['uuid'])
        self.assertEqual(self.node['id'], port.node_id)

    def test_create_port_node_uuid_not_found(self):
        pdict = post_get_test_port(
            node_uuid='1a1a1a1a-2b2b-3c3c-4d4d-5e5e5e5e5e5e')
        response = self.post_json('/ports', pdict, expect_errors=True)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.BAD_REQUEST, response.status_int)
        self.assertTrue(response.json['error_message'])

    def test_create_port_portgroup_uuid_not_found(self):
        pdict = post_get_test_port(
            portgroup_uuid='1a1a1a1a-2b2b-3c3c-4d4d-5e5e5e5e5e5e')
        response = self.post_json('/ports', pdict, expect_errors=True,
                                  headers=self.headers)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.BAD_REQUEST, response.status_int)
        self.assertTrue(response.json['error_message'])

    def test_create_port_portgroup_uuid_not_found_old_api_version(self):
        pdict = post_get_test_port(
            portgroup_uuid='1a1a1a1a-2b2b-3c3c-4d4d-5e5e5e5e5e5e')
        response = self.post_json('/ports', pdict, expect_errors=True)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.NOT_ACCEPTABLE, response.status_int)
        self.assertTrue(response.json['error_message'])

    def test_create_port_portgroup(self):
        pdict = post_get_test_port(
            portgroup_uuid=self.portgroup.uuid,
            node_uuid=self.node.uuid)

        response = self.post_json('/ports', pdict, headers=self.headers)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.CREATED, response.status_int)

    def test_create_port_portgroup_different_nodes(self):
        pdict = post_get_test_port(
            portgroup_uuid=self.portgroup.uuid,
            node_uuid=uuidutils.generate_uuid())

        response = self.post_json('/ports', pdict, headers=self.headers,
                                  expect_errors=True)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.BAD_REQUEST, response.status_int)

    def test_create_port_portgroup_old_api_version(self):
        pdict = post_get_test_port(
            portgroup_uuid=self.portgroup.uuid,
            node_uuid=self.node.uuid
        )
        headers = {api_base.Version.string: '1.15'}
        response = self.post_json('/ports', pdict, expect_errors=True,
                                  headers=headers)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.NOT_ACCEPTABLE, response.status_int)

    def test_create_port_address_already_exist(self):
        address = 'AA:AA:AA:11:22:33'
        pdict = post_get_test_port(address=address)
        self.post_json('/ports', pdict, headers=self.headers)
        pdict['uuid'] = uuidutils.generate_uuid()
        response = self.post_json('/ports', pdict, expect_errors=True,
                                  headers=self.headers)
        self.assertEqual(http_client.CONFLICT, response.status_int)
        self.assertEqual('application/json', response.content_type)
        error_msg = response.json['error_message']
        self.assertTrue(error_msg)
        self.assertIn(address, error_msg.upper())

    def test_create_port_with_internal_field(self):
        pdict = post_get_test_port()
        pdict['internal_info'] = {'a': 'b'}
        response = self.post_json('/ports', pdict, expect_errors=True)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.BAD_REQUEST, response.status_int)
        self.assertTrue(response.json['error_message'])

    def test_create_port_some_invalid_local_link_connection_key(self):
        pdict = post_get_test_port(
            local_link_connection={'switch_id': 'value1',
                                   'port_id': 'Ethernet1/15',
                                   'switch_foo': 'value3'})
        response = self.post_json('/ports', pdict, expect_errors=True,
                                  headers=self.headers)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.BAD_REQUEST, response.status_int)
        self.assertTrue(response.json['error_message'])

    def test_create_port_local_link_connection_keys(self):
        pdict = post_get_test_port(
            local_link_connection={'switch_id': '0a:1b:2c:3d:4e:5f',
                                   'port_id': 'Ethernet1/15',
                                   'switch_info': 'value3'})
        response = self.post_json('/ports', pdict, headers=self.headers)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.CREATED, response.status_int)

    def test_create_port_local_link_connection_switch_id_bad_mac(self):
        pdict = post_get_test_port(
            local_link_connection={'switch_id': 'zz:zz:zz:zz:zz:zz',
                                   'port_id': 'Ethernet1/15',
                                   'switch_info': 'value3'})
        response = self.post_json('/ports', pdict, expect_errors=True,
                                  headers=self.headers)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.BAD_REQUEST, response.status_int)
        self.assertTrue(response.json['error_message'])

    def test_create_port_local_link_connection_missing_mandatory(self):
        pdict = post_get_test_port(
            local_link_connection={'switch_id': '0a:1b:2c:3d:4e:5f',
                                   'switch_info': 'fooswitch'})
        response = self.post_json('/ports', pdict, expect_errors=True,
                                  headers=self.headers)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.BAD_REQUEST, response.status_int)

    def test_create_port_local_link_connection_missing_optional(self):
        pdict = post_get_test_port(
            local_link_connection={'switch_id': '0a:1b:2c:3d:4e:5f',
                                   'port_id': 'Ethernet1/15'})
        response = self.post_json('/ports', pdict, headers=self.headers)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.CREATED, response.status_int)

    def test_create_port_with_llc_old_api_version(self):
        headers = {api_base.Version.string: '1.14'}
        pdict = post_get_test_port(
            local_link_connection={'switch_id': '0a:1b:2c:3d:4e:5f',
                                   'port_id': 'Ethernet1/15'})
        response = self.post_json('/ports', pdict, headers=headers,
                                  expect_errors=True)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.NOT_ACCEPTABLE, response.status_int)

    def test_create_port_with_pxe_enabled_old_api_version(self):
        headers = {api_base.Version.string: '1.14'}
        pdict = post_get_test_port(pxe_enabled=False)
        del pdict['local_link_connection']
        del pdict['portgroup_uuid']
        response = self.post_json('/ports', pdict, headers=headers,
                                  expect_errors=True)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.NOT_ACCEPTABLE, response.status_int)

    def test_portgroups_subresource_post(self):
        headers = {api_base.Version.string: '1.24'}
        pdict = post_get_test_port()
        response = self.post_json('/portgroups/%s/ports' % self.portgroup.uuid,
                                  pdict, headers=headers, expect_errors=True)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.FORBIDDEN, response.status_int)

    @mock.patch.object(common_utils, 'warn_about_deprecated_extra_vif_port_id',
                       autospec=True)
    def test_create_port_with_extra_vif_port_id_deprecated(self, mock_warn):
        pdict = post_get_test_port(pxe_enabled=False,
                                   extra={'vif_port_id': 'foo'})
        response = self.post_json('/ports', pdict, headers=self.headers)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_client.CREATED, response.status_int)
        self.assertEqual(1, mock_warn.call_count)

    def _test_create_port(self, has_vif=False, in_portgroup=False,
                          pxe_enabled=True, standalone_ports=True,
                          http_status=http_client.CREATED):
        extra = {}
        if has_vif:
            extra = {'vif_port_id': uuidutils.generate_uuid()}
        pdict = post_get_test_port(
            node_uuid=self.node.uuid,
            pxe_enabled=pxe_enabled,
            extra=extra)

        if not in_portgroup:
            pdict.pop('portgroup_uuid')
        else:
            self.portgroup.standalone_ports_supported = standalone_ports
            self.portgroup.save()

        expect_errors = http_status != http_client.CREATED

        response = self.post_json('/ports', pdict, headers=self.headers,
                                  expect_errors=expect_errors)
        self.assertEqual('application/json', response.content_type)
        self.assertEqual(http_status, response.status_int)
        if not expect_errors:
            expected_portgroup_uuid = pdict.get('portgroup_uuid', None)
            self.assertEqual(expected_portgroup_uuid,
                             response.json['portgroup_uuid'])
            self.assertEqual(extra, response.json['extra'])

    def test_create_port_novif_pxe_noportgroup(self):
        self._test_create_port(has_vif=False, in_portgroup=False,
                               pxe_enabled=True,
                               http_status=http_client.CREATED)

    def test_create_port_novif_nopxe_noportgroup(self):
        self._test_create_port(has_vif=False, in_portgroup=False,
                               pxe_enabled=False,
                               http_status=http_client.CREATED)

    def test_create_port_vif_pxe_noportgroup(self):
        self._test_create_port(has_vif=True, in_portgroup=False,
                               pxe_enabled=True,
                               http_status=http_client.CREATED)

    def test_create_port_vif_nopxe_noportgroup(self):
        self._test_create_port(has_vif=True, in_portgroup=False,
                               pxe_enabled=False,
                               http_status=http_client.CREATED)

    def test_create_port_novif_pxe_portgroup_standalone_ports(self):
        self._test_create_port(has_vif=False, in_portgroup=True,
                               pxe_enabled=True,
                               standalone_ports=True,
                               http_status=http_client.CREATED)

    def test_create_port_novif_pxe_portgroup_nostandalone_ports(self):
        self._test_create_port(has_vif=False, in_portgroup=True,
                               pxe_enabled=True,
                               standalone_ports=False,
                               http_status=http_client.CONFLICT)

    def test_create_port_novif_nopxe_portgroup_standalone_ports(self):
        self._test_create_port(has_vif=False, in_portgroup=True,
                               pxe_enabled=False,
                               standalone_ports=True,
                               http_status=http_client.CREATED)

    def test_create_port_novif_nopxe_portgroup_nostandalone_ports(self):
        self._test_create_port(has_vif=False, in_portgroup=True,
                               pxe_enabled=False,
                               standalone_ports=False,
                               http_status=http_client.CREATED)

    def test_create_port_vif_pxe_portgroup_standalone_ports(self):
        self._test_create_port(has_vif=True, in_portgroup=True,
                               pxe_enabled=True,
                               standalone_ports=True,
                               http_status=http_client.CREATED)

    def test_create_port_vif_pxe_portgroup_nostandalone_ports(self):
        self._test_create_port(has_vif=True, in_portgroup=True,
                               pxe_enabled=True,
                               standalone_ports=False,
                               http_status=http_client.CONFLICT)

    def test_create_port_vif_nopxe_portgroup_standalone_ports(self):
        self._test_create_port(has_vif=True, in_portgroup=True,
                               pxe_enabled=False,
                               standalone_ports=True,
                               http_status=http_client.CREATED)

    def test_create_port_vif_nopxe_portgroup_nostandalone_ports(self):
        self._test_create_port(has_vif=True, in_portgroup=True,
                               pxe_enabled=False,
                               standalone_ports=False,
                               http_status=http_client.CONFLICT)


@mock.patch.object(rpcapi.ConductorAPI, 'destroy_port')
class TestDelete(test_api_base.BaseApiTest):

    def setUp(self):
        super(TestDelete, self).setUp()
        self.node = obj_utils.create_test_node(self.context)
        self.port = obj_utils.create_test_port(self.context,
                                               node_id=self.node.id)

        gtf = mock.patch.object(rpcapi.ConductorAPI, 'get_topic_for')
        self.mock_gtf = gtf.start()
        self.mock_gtf.return_value = 'test-topic'
        self.addCleanup(gtf.stop)

    def test_delete_port_byaddress(self, mock_dpt):
        response = self.delete('/ports/%s' % self.port.address,
                               expect_errors=True)
        self.assertEqual(http_client.BAD_REQUEST, response.status_int)
        self.assertEqual('application/json', response.content_type)
        self.assertIn(self.port.address, response.json['error_message'])

    @mock.patch.object(notification_utils, '_emit_api_notification')
    def test_delete_port_byid(self, mock_notify, mock_dpt):
        self.delete('/ports/%s' % self.port.uuid, expect_errors=True)
        self.assertTrue(mock_dpt.called)
        mock_notify.assert_has_calls([mock.call(mock.ANY, mock.ANY, 'delete',
                                      obj_fields.NotificationLevel.INFO,
                                      obj_fields.NotificationStatus.START,
                                      node_uuid=self.node.uuid),
                                      mock.call(mock.ANY, mock.ANY, 'delete',
                                      obj_fields.NotificationLevel.INFO,
                                      obj_fields.NotificationStatus.END,
                                      node_uuid=self.node.uuid)])

    @mock.patch.object(notification_utils, '_emit_api_notification')
    def test_delete_port_node_locked(self, mock_notify, mock_dpt):
        self.node.reserve(self.context, 'fake', self.node.uuid)
        mock_dpt.side_effect = exception.NodeLocked(node='fake-node',
                                                    host='fake-host')
        ret = self.delete('/ports/%s' % self.port.uuid, expect_errors=True)
        self.assertEqual(http_client.CONFLICT, ret.status_code)
        self.assertTrue(ret.json['error_message'])
        self.assertTrue(mock_dpt.called)
        mock_notify.assert_has_calls([mock.call(mock.ANY, mock.ANY, 'delete',
                                      obj_fields.NotificationLevel.INFO,
                                      obj_fields.NotificationStatus.START,
                                      node_uuid=self.node.uuid),
                                      mock.call(mock.ANY, mock.ANY, 'delete',
                                      obj_fields.NotificationLevel.ERROR,
                                      obj_fields.NotificationStatus.ERROR,
                                      node_uuid=self.node.uuid)])

    def test_portgroups_subresource_delete(self, mock_dpt):
        portgroup = obj_utils.create_test_portgroup(self.context,
                                                    node_id=self.node.id)
        port = obj_utils.create_test_port(self.context, node_id=self.node.id,
                                          uuid=uuidutils.generate_uuid(),
                                          portgroup_id=portgroup.id,
                                          address='52:55:00:cf:2d:31')
        headers = {api_base.Version.string: '1.24'}
        response = self.delete(
            '/portgroups/%(portgroup)s/ports/%(port)s' %
            {'portgroup': portgroup.uuid, 'port': port.uuid},
            headers=headers, expect_errors=True)
        self.assertEqual(http_client.FORBIDDEN, response.status_int)
        self.assertEqual('application/json', response.content_type)
