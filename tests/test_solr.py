# Copyright 2012 10gen, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# This file will be used with PyPi in order to package and distribute the final
# product.

"""Test Solr search using the synchronizer, i.e. as it would be used by an user
    """
import os
import time
import unittest
import sys
import inspect

CURRENT_DIR = inspect.getfile(inspect.currentframe())
CMD_DIR = os.path.realpath(os.path.abspath(os.path.split(CURRENT_DIR)[0]))
DOC_DIR = CMD_DIR.rsplit("/", 1)[0]
DOC_DIR += '/doc_managers'
if DOC_DIR not in sys.path:
    sys.path.insert(0, DOC_DIR)

TEST = CMD_DIR

if TEST not in sys.path:
    sys.path.insert(0, TEST)

MONGO = CMD_DIR.rsplit("/", 1)[0]
MONGO += "/mongo_connector"
if MONGO not in sys.path:
    sys.path.insert(0, MONGO)

try:
    from pymongo import MongoClient as Connection
except ImportError:
    from pymongo import Connection    

from setup_cluster import kill_mongo_proc, start_mongo_proc, start_cluster
from pysolr import Solr, SolrError
from mongo_connector import Connector
from optparse import OptionParser
from pymongo.errors import OperationFailure, AutoReconnect
from requests.exceptions import MissingSchema


PORTS_ONE = {"PRIMARY": "27117", "SECONDARY": "27118", "ARBITER": "27119",
             "CONFIG": "27220", "MAIN": "27217"}
NUMBER_OF_DOC_DIRS = 100


class TestSynchronizer(unittest.TestCase):
    """ Tests Solr
    """

    def runTest(self):
        """ Runs tests
        """
        unittest.TestCase.__init__(self)

    @classmethod
    def setUpClass(cls):
        cls.flag = start_cluster()
        if cls.flag:
            cls.conn = Connection('localhost:' + PORTS_ONE['MAIN'],
                replicaSet="demo-repl")
            # Creating a Solr object with an invalid URL 
            # doesn't create an exception
            cls.solr_conn = Solr('http://localhost:8983/solr')
            try:
                cls.solr_conn.commit()
            except (SolrError, MissingSchema):
                cls.err_msg = "Cannot connect to Solr!"
                cls.flag = False
            if cls.flag:    
                cls.solr_conn.delete(q='*:*')
        else:
            cls.err_msg = "Shards cannot be added to mongos"        

    def setUp(self):
        if not self.flag:
            self.fail(self.err_msg)

        self.connector = Connector('localhost:' + PORTS_ONE["MAIN"], 
            'config.txt', 'http://localhost:8983/solr', ['test.test'], '_id',
            None, 
            '../mongo_connector/doc_managers/solr_doc_manager.py')
        self.connector.start()
        while len(self.connector.shard_set) == 0:
            time.sleep(1)
        count = 0
        while (True):
            try:
                self.conn['test']['test'].remove(safe=True)
                break
            except (AutoReconnect, OperationFailure):
                time.sleep(1)
                count += 1
                if count > 60:
                    unittest.SkipTest('Call to remove failed too '
                    'many times in setup')
        while (len(self.solr_conn.search('*:*')) != 0):
            time.sleep(1)

    def tearDown(self):
        self.connector.doc_manager.auto_commit = False
        time.sleep(2)
        self.connector.join()

    def test_shard_length(self):
        """Tests the shard_length to see if the shard set was recognized
        """

        self.assertEqual(len(self.connector.shard_set), 1)

    def test_initial(self):
        """Tests search and assures that the databases are clear.
        """

        while (True):
            try:
                self.conn['test']['test'].remove(safe=True)
                break
            except OperationFailure:
                continue

        self.solr_conn.delete(q='*:*')
        self.assertEqual(self.conn['test']['test'].find().count(), 0)
        self.assertEqual(len(self.solr_conn.search('*:*')), 0)

    def test_insert(self):
        """Tests insert
        """

        self.conn['test']['test'].insert({'name': 'paulie'}, safe=True)
        while (len(self.solr_conn.search('*:*')) == 0):
            time.sleep(1)
        result_set_1 = self.solr_conn.search('paulie')
        self.assertEqual(len(result_set_1), 1)
        result_set_2 = self.conn['test']['test'].find_one()
        for item in result_set_1:
            self.assertEqual(item['_id'], str(result_set_2['_id']))
            self.assertEqual(item['name'], result_set_2['name'])

    def test_remove(self):
        """Tests remove
        """

        self.conn['test']['test'].remove({'name': 'paulie'}, safe=True)
        while (len(self.solr_conn.search('*:*')) == 1):
            time.sleep(1)
        result_set_1 = self.solr_conn.search('paulie')
        self.assertEqual(len(result_set_1), 0)

    def test_rollback(self):
        """Tests rollback. We force a rollback by inserting one doc, killing
            primary, adding another doc, killing the new primary, and
            restarting both the servers.
        """

        primary_conn = Connection('localhost', int(PORTS_ONE['PRIMARY']))

        self.conn['test']['test'].insert({'name': 'paul'}, safe=True)
        while self.conn['test']['test'].find({'name': 'paul'}).count() != 1:
            time.sleep(1)
        while len(self.solr_conn.search('*:*')) != 1:
            time.sleep(1)
        kill_mongo_proc('localhost', PORTS_ONE['PRIMARY'])

        new_primary_conn = Connection('localhost', int(PORTS_ONE['SECONDARY']))
        admin_db = new_primary_conn['admin']
        while admin_db.command("isMaster")['ismaster'] is False:
            time.sleep(1)
        time.sleep(5)
        count = 0
        while True:
            try:
                self.conn['test']['test'].insert(
                    {'name': 'pauline'}, safe=True)
                break
            except OperationFailure:
                count += 1
                if count > 60:
                    self.fail('Call to insert failed too ' 
                        'many times in test_rollback')
                time.sleep(1)
                continue

        while (len(self.solr_conn.search('*:*')) != 2):
            time.sleep(1)

        result_set_1 = self.solr_conn.search('pauline')
        result_set_2 = self.conn['test']['test'].find_one({'name': 'pauline'})
        self.assertEqual(len(result_set_1), 1)
        for item in result_set_1:
            self.assertEqual(item['_id'], str(result_set_2['_id']))
        kill_mongo_proc('localhost', PORTS_ONE['SECONDARY'])

        start_mongo_proc(PORTS_ONE['PRIMARY'], "demo-repl", "/replset1a",
                       "/replset1a.log", None)

        while primary_conn['admin'].command("isMaster")['ismaster'] is False:
            time.sleep(1)

        start_mongo_proc(PORTS_ONE['SECONDARY'], "demo-repl", "/replset1b",
                       "/replset1b.log", None)

        time.sleep(2)
        result_set_1 = self.solr_conn.search('pauline')
        self.assertEqual(len(result_set_1), 0)
        result_set_2 = self.solr_conn.search('paul')
        self.assertEqual(len(result_set_2), 1)

    def test_stress(self):
        """Test stress by inserting and removing a large amount of docs.
        """
        #stress test
        for i in range(0, NUMBER_OF_DOC_DIRS):
            self.conn['test']['test'].insert({'name': 'Paul ' + str(i)})
        time.sleep(5)
        while  (len(self.solr_conn.search('*:*', rows=NUMBER_OF_DOC_DIRS))
                != NUMBER_OF_DOC_DIRS):
            time.sleep(5)
        for i in range(0, NUMBER_OF_DOC_DIRS):
            result_set_1 = self.solr_conn.search('Paul ' + str(i))
            for item in result_set_1:
                self.assertEqual(item['_id'], item['_id'])

    def test_stressed_rollback(self):
        """Test stressed rollback with number of documents equal to specified
        in global variable. The rollback is performed the same way as before
            but with more docs
        """

        self.conn['test']['test'].remove()
        while len(self.solr_conn.search('*:*', rows=NUMBER_OF_DOC_DIRS)) != 0:
            time.sleep(1)
        for i in range(0, NUMBER_OF_DOC_DIRS):
            self.conn['test']['test'].insert(
                {'name': 'Paul ' + str(i)}, safe=True)

        while (len(self.solr_conn.search('*:*', rows=NUMBER_OF_DOC_DIRS)) 
                != NUMBER_OF_DOC_DIRS):
            time.sleep(1)
        primary_conn = Connection('localhost', int(PORTS_ONE['PRIMARY']))
        kill_mongo_proc('localhost', PORTS_ONE['PRIMARY'])

        new_primary_conn = Connection('localhost', int(PORTS_ONE['SECONDARY']))
        admin_db = new_primary_conn['admin']

        while admin_db.command("isMaster")['ismaster'] is False:
            time.sleep(1)
        time.sleep(5)
        count = -1
        while count + 1 < NUMBER_OF_DOC_DIRS:
            try:
                count += 1
                self.conn['test']['test'].insert(
                    {'name': 'Pauline ' + str(count)},
                                            safe=True)
            except (OperationFailure, AutoReconnect):
                time.sleep(1)

        while (len(self.solr_conn.search('*:*', rows=NUMBER_OF_DOC_DIRS * 2)) !=
               self.conn['test']['test'].find().count()):
            time.sleep(1)
        result_set_1 = self.solr_conn.search('Pauline', 
            rows=NUMBER_OF_DOC_DIRS * 2, sort='_id asc')
        for item in result_set_1:
            result_set_2 = self.conn['test']['test'].find_one(
                {'name': item['name']})
            self.assertEqual(item['_id'], str(result_set_2['_id']))

        kill_mongo_proc('localhost', PORTS_ONE['SECONDARY'])
        start_mongo_proc(PORTS_ONE['PRIMARY'], "demo-repl", "/replset1a",
                       "/replset1a.log", None)

        while primary_conn['admin'].command("isMaster")['ismaster'] is False:
            time.sleep(1)

        start_mongo_proc(PORTS_ONE['SECONDARY'], "demo-repl", "/replset1b",
                       "/replset1b.log", None)

        while (len(self.solr_conn.search('Pauline',
                rows=NUMBER_OF_DOC_DIRS * 2)) != 0):
            time.sleep(15)
        result_set_1 = self.solr_conn.search('Pauline',
            rows=NUMBER_OF_DOC_DIRS * 2)
        self.assertEqual(len(result_set_1), 0)
        result_set_2 = self.solr_conn.search('Paul', 
            rows=NUMBER_OF_DOC_DIRS * 2)
        self.assertEqual(len(result_set_2), NUMBER_OF_DOC_DIRS)

if __name__ == '__main__':
    os.system('rm config.txt; touch config.txt')
    PARSER = OptionParser()

    #-m is for the main address, which is a host:port pair, ideally of the
    #mongos. For non sharded clusters, it can be the primary.
    PARSER.add_option("-m", "--main", action="store", type="string",
                      dest="main_addr", default="27217")

    (OPTIONS, ARGS) = PARSER.parse_args()
    PORTS_ONE['MAIN'] = OPTIONS.main_addr

    unittest.main(argv=[sys.argv[0]])
