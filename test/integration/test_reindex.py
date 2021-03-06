import elasticsearch
import curator
import os
import json
import string, random, tempfile
import click
from click import testing as clicktest
import time

from . import CuratorTestCase
from . import testvars as testvars

import logging
logger = logging.getLogger(__name__)

host,  port  = os.environ.get('TEST_ES_SERVER',   'localhost:9200').split(':')
rhost, rport = os.environ.get('REMOTE_ES_SERVER', 'localhost:9201').split(':')
port  = int(port)  if port  else 9200
rport = int(rport) if rport else 9201

class TestCLIReindex(CuratorTestCase):
    def test_reindex_manual(self):
        wait_interval = 1
        max_wait = 3
        source = 'my_source'
        dest = 'my_dest'
        expected = 3

        self.create_index(source)
        self.add_docs(source)
        self.write_config(
            self.args['configfile'], testvars.client_config.format(host, port))
        self.write_config(self.args['actionfile'],
            testvars.reindex.format(wait_interval, max_wait, source, dest))
        test = clicktest.CliRunner()
        result = test.invoke(
                    curator.cli,
                    [
                        '--config', self.args['configfile'],
                        self.args['actionfile']
                    ],
                    )
        self.assertEqual(expected, self.client.count(index=dest)['count'])
    def test_reindex_selected(self):
        wait_interval = 1
        max_wait = 3
        source = 'my_source'
        dest = 'my_dest'
        expected = 3

        self.create_index(source)
        self.add_docs(source)
        self.write_config(
            self.args['configfile'], testvars.client_config.format(host, port))
        self.write_config(self.args['actionfile'],
            testvars.reindex.format(wait_interval, max_wait, 'REINDEX_SELECTION', dest))
        test = clicktest.CliRunner()
        result = test.invoke(
                    curator.cli,
                    [
                        '--config', self.args['configfile'],
                        self.args['actionfile']
                    ],
                    )
        self.assertEqual(expected, self.client.count(index=dest)['count'])
    def test_reindex_empty_list(self):
        wait_interval = 1
        max_wait = 3
        source = 'my_source'
        dest = 'my_dest'
        expected = '.tasks'

        self.write_config(
            self.args['configfile'], testvars.client_config.format(host, port))
        self.write_config(self.args['actionfile'],
            testvars.reindex.format(wait_interval, max_wait, source, dest))
        test = clicktest.CliRunner()
        result = test.invoke(
                    curator.cli,
                    [
                        '--config', self.args['configfile'],
                        self.args['actionfile']
                    ],
                    )
        self.assertEqual(expected, curator.get_indices(self.client)[0])
    def test_reindex_selected_many_to_one(self):
        wait_interval = 1
        max_wait = 3
        source1 = 'my_source1'
        source2 = 'my_source2'
        dest = 'my_dest'
        expected = 6

        self.create_index(source1)
        self.add_docs(source1)
        self.create_index(source2)
        for i in ["4", "5", "6"]:
            self.client.create(
                index=source2, doc_type='log', id=i,
                body={"doc" + i :'TEST DOCUMENT'},
            )
            self.client.indices.flush(index=source2, force=True)
        self.write_config(
            self.args['configfile'], testvars.client_config.format(host, port))
        self.write_config(self.args['actionfile'],
            testvars.reindex.format(wait_interval, max_wait, 'REINDEX_SELECTION', dest))
        test = clicktest.CliRunner()
        result = test.invoke(
                    curator.cli,
                    [
                        '--config', self.args['configfile'],
                        self.args['actionfile']
                    ],
                    )
        self.assertEqual(expected, self.client.count(index=dest)['count'])
    def test_reindex_from_remote(self):
        wait_interval = 1
        max_wait = 3
        source1 = 'my_source1'
        source2 = 'my_source2'
        prefix = 'my_'
        dest = 'my_dest'
        expected = 6

        # Build remote client
        rclient = curator.get_client(host=rhost, port=rport)
        # Build indices remotely.
        counter = 0
        for rindex in [source1, source2]:
            rclient.indices.create(index=rindex)
            for i in range(0, 3):
                rclient.create(
                    index=rindex, doc_type='log', id=str(counter+1),
                    body={"doc" + str(counter+i) :'TEST DOCUMENT'},
                )
                counter += 1
                rclient.indices.flush(index=rindex, force=True)
        self.write_config(
            self.args['configfile'], testvars.client_config.format(host, port))
        self.write_config(self.args['actionfile'],
            testvars.remote_reindex.format(
                wait_interval, 
                max_wait, 
                'http://{0}:{1}'.format(rhost, rport),
                'REINDEX_SELECTION', 
                dest,
                prefix
            )
        )
        test = clicktest.CliRunner()
        result = test.invoke(
                    curator.cli,
                    [
                        '--config', self.args['configfile'],
                        self.args['actionfile']
                    ],
                    )
        # Do our own cleanup here.
        rclient.indices.delete(index='{0},{1}'.format(source1, source2))
        self.assertEqual(expected, self.client.count(index=dest)['count'])
    def test_reindex_from_remote_no_connection(self):
        wait_interval = 1
        max_wait = 3
        bad_port = 70000
        dest = 'my_dest'
        expected = 1
        self.write_config(
            self.args['configfile'], testvars.client_config.format(host, port))
        self.write_config(self.args['actionfile'],
            testvars.remote_reindex.format(
                wait_interval, 
                max_wait, 
                'http://{0}:{1}'.format(rhost, bad_port),
                'REINDEX_SELECTION', 
                dest,
                'my_'
            )
        )
        test = clicktest.CliRunner()
        result = test.invoke(
                    curator.cli,
                    [
                        '--config', self.args['configfile'],
                        self.args['actionfile']
                    ],
                    )
        self.assertEqual(expected, result.exit_code)
    def test_reindex_from_remote_no_indices(self):
        wait_interval = 1
        max_wait = 3
        source1 = 'wrong1'
        source2 = 'wrong2'
        prefix = 'my_'
        dest = 'my_dest'
        expected = 1

        # Build remote client
        rclient = curator.get_client(host=rhost, port=rport)
        # Build indices remotely.
        counter = 0
        for rindex in [source1, source2]:
            rclient.indices.create(index=rindex)
            for i in range(0, 3):
                rclient.create(
                    index=rindex, doc_type='log', id=str(counter+1),
                    body={"doc" + str(counter+i) :'TEST DOCUMENT'},
                )
                counter += 1
                rclient.indices.flush(index=rindex, force=True)        
        self.write_config(
            self.args['configfile'], testvars.client_config.format(host, port))
        self.write_config(self.args['actionfile'],
            testvars.remote_reindex.format(
                wait_interval, 
                max_wait, 
                'http://{0}:{1}'.format(rhost, rport),
                'REINDEX_SELECTION', 
                dest,
                prefix
            )
        )
        test = clicktest.CliRunner()
        result = test.invoke(
                    curator.cli,
                    [
                        '--config', self.args['configfile'],
                        self.args['actionfile']
                    ],
                    )
        # Do our own cleanup here.
        rclient.indices.delete(index='{0},{1}'.format(source1, source2))
        self.assertEqual(expected, result.exit_code)
