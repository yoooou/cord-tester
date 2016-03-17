import unittest
from nose.tools import *
from nose.twistedtools import reactor, deferred
from twisted.internet import defer
from scapy.all import *
import time, monotonic
import os, sys
import tempfile
import random
import threading
from Stats import Stats
from OnosCtrl import OnosCtrl
from DHCP import DHCPTest
from EapTLS import TLSAuthTest
from Channels import Channels, IgmpChannel
from subscriberDb import SubscriberDB
from threadPool import ThreadPool
log.setLevel('INFO')

class Subscriber(Channels):

      STATS_RX = 0
      STATS_TX = 1
      STATS_JOIN = 2
      STATS_LEAVE = 3
      SUBSCRIBER_SERVICES = 'DHCP IGMP TLS'
      def __init__(self, name = 'sub', service = SUBSCRIBER_SERVICES, num = 1, channel_start = 0, 
                   iface = 'veth0', iface_mcast = 'veth2', 
                   mcast_cb = None, loginType = 'wireless'):
            Channels.__init__(self, num, channel_start = channel_start, 
                              iface = iface, iface_mcast = iface_mcast, mcast_cb = mcast_cb)
            self.name = name
            self.service = service
            self.service_map = {}
            services = self.service.strip().split(' ')
            for s in services:
                  self.service_map[s] = True
            self.loginType = loginType
            ##start streaming channels
            self.join_map = {}
            ##accumulated join recv stats
            self.join_rx_stats = Stats()

      def has_service(self, service):
            if self.service_map.has_key(service):
                  return self.service_map[service]
            if self.service_map.has_key(service.upper()):
                  return self.service_map[service.upper()]
            return False

      def channel_join_update(self, chan, join_time):
            self.join_map[chan] = ( Stats(), Stats(), Stats(), Stats() )
            self.channel_update(chan, self.STATS_JOIN, 1, t = join_time)

      def channel_join(self, chan = 0, delay = 2):
            '''Join a channel and create a send/recv stats map'''
            if self.join_map.has_key(chan):
                  del self.join_map[chan]
            self.delay = delay
            chan, join_time = self.join(chan)
            self.channel_join_update(chan, join_time)
            return chan

      def channel_join_next(self, delay = 2):
            '''Joins the next channel leaving the last channel'''
            if self.last_chan:
                  if self.join_map.has_key(self.last_chan):
                        del self.join_map[self.last_chan]
            self.delay = delay
            chan, join_time = self.join_next()
            self.channel_join_update(chan, join_time)
            return chan

      def channel_jump(self, delay = 2):
            '''Jumps randomly to the next channel leaving the last channel'''
            if self.last_chan is not None:
                  if self.join_map.has_key(self.last_chan):
                        del self.join_map[self.last_chan]
            self.delay = delay
            chan, join_time = self.jump()
            self.channel_join_update(chan, join_time)
            return chan

      def channel_leave(self, chan = 0):
            if self.join_map.has_key(chan):
                  del self.join_map[chan]
            self.leave(chan)

      def channel_update(self, chan, stats_type, packets, t=0):
            if type(chan) == type(0):
                  chan_list = (chan,)
            else:
                  chan_list = chan
            for c in chan_list: 
                  if self.join_map.has_key(c):
                        self.join_map[c][stats_type].update(packets = packets, t = t)

      def channel_receive(self, chan, cb = None, count = 1):
            log.info('Subscriber %s receiving from group %s, channel %d' %(self.name, self.gaddr(chan), chan))
            self.recv(chan, cb = cb, count = count)

      def recv_channel_cb(self, pkt):
            ##First verify that we have received the packet for the joined instance
            log.debug('Packet received for group %s, subscriber %s' %(pkt[IP].dst, self.name))
            chan = self.caddr(pkt[IP].dst)
            assert_equal(chan in self.join_map.keys(), True)
            recv_time = monotonic.monotonic() * 1000000
            join_time = self.join_map[chan][self.STATS_JOIN].start
            delta = recv_time - join_time
            self.join_rx_stats.update(packets=1, t = delta, usecs = True)
            self.channel_update(chan, self.STATS_RX, 1, t = delta)
            log.debug('Packet received in %.3f usecs for group %s after join' %(delta, pkt[IP].dst))

class subscriber_pool:

      def __init__(self, subscriber, test_cbs):
            self.subscriber = subscriber
            self.test_cbs = test_cbs

      def pool_cb(self):
            for cb in self.test_cbs:
                  if cb:
                        cb(self.subscriber)
      
class subscriber_exchange(unittest.TestCase):

      apps = [ 'org.onosproject.aaa', 'org.onosproject.dhcp' ]

      dhcp_server_config = {
        "ip": "10.1.11.50",
        "mac": "ca:fe:ca:fe:ca:fe",
        "subnet": "255.255.252.0",
        "broadcast": "10.1.11.255",
        "router": "10.1.8.1",
        "domain": "8.8.8.8",
        "ttl": "63",
        "delay": "2",
        "startip": "10.1.11.51",
        "endip": "10.1.11.100"
      }

      aaa_loaded = False

      def setUp(self):
          ''' Activate the dhcp and igmp apps'''
          for app in self.apps:
              onos_ctrl = OnosCtrl(app)
              status, _ = onos_ctrl.activate()
              assert_equal(status, True)
              time.sleep(2)

      def teardown(self):
          '''Deactivate the dhcp app'''
          for app in self.apps:
              onos_ctrl = OnosCtrl(app)
              onos_ctrl.deactivate()

      def onos_aaa_load(self):
            if self.aaa_loaded:
                  return
            aaa_dict = {'apps' : { 'org.onosproject.aaa' : { 'AAA' : { 'radiusSecret': 'radius_password', 
                                                                       'radiusIp': '172.17.0.2' } } } }
            radius_ip = os.getenv('ONOS_AAA_IP') or '172.17.0.2'
            aaa_dict['apps']['org.onosproject.aaa']['AAA']['radiusIp'] = radius_ip
            self.onos_load_config('org.onosproject.aaa', aaa_dict)
            self.aaa_loaded = True

      def onos_dhcp_table_load(self, config = None):
          dhcp_dict = {'apps' : { 'org.onosproject.dhcp' : { 'dhcp' : copy.copy(self.dhcp_server_config) } } }
          dhcp_config = dhcp_dict['apps']['org.onosproject.dhcp']['dhcp']
          if config:
              for k in config.keys():
                  if dhcp_config.has_key(k):
                      dhcp_config[k] = config[k]
          self.onos_load_config('org.onosproject.dhcp', dhcp_dict)

      def onos_load_config(self, app, config):
          onos_ctrl = OnosCtrl(app)
          status, code = onos_ctrl.config(config)
          if status is False:
             log.info('JSON config request for app %s returned status %d' %(app, code))
             assert_equal(status, True)
          time.sleep(2)

      def dhcp_sndrcv(self, update_seed = False):
            cip, sip = self.dhcp.discover(update_seed = update_seed)
            assert_not_equal(cip, None)
            assert_not_equal(sip, None)
            log.info('Got dhcp client IP %s from server %s for mac %s' %
                     (cip, sip, self.dhcp.get_mac(cip)[0]))
            return cip,sip

      def dhcp_request(self, seed_ip = '10.10.10.1', iface = 'veth0', update_seed = False):
            config = {'startip':'10.10.10.20', 'endip':'10.10.10.69',
                      'ip':'10.10.10.2', 'mac': "ca:fe:ca:fe:ca:fe",
                      'subnet': '255.255.255.0', 'broadcast':'10.10.10.255', 'router':'10.10.10.1'}
            self.onos_dhcp_table_load(config)
            self.dhcp = DHCPTest(seed_ip = seed_ip, iface = iface)
            cip, sip = self.dhcp_sndrcv(update_seed = update_seed)
            return cip, sip

      def recv_channel_cb(self, pkt):
            ##First verify that we have received the packet for the joined instance
            chan = self.subscriber.caddr(pkt[IP].dst)
            assert_equal(chan in self.subscriber.join_map.keys(), True)
            recv_time = monotonic.monotonic() * 1000000
            join_time = self.subscriber.join_map[chan][self.subscriber.STATS_JOIN].start
            delta = recv_time - join_time
            self.subscriber.join_rx_stats.update(packets=1, t = delta, usecs = True)
            self.subscriber.channel_update(chan, self.subscriber.STATS_RX, 1, t = delta)
            log.debug('Packet received in %.3f usecs for group %s after join' %(delta, pkt[IP].dst))
            self.test_status = True

      def tls_verify(self, subscriber):
            if subscriber.has_service('TLS'):
                  time.sleep(2)
                  tls = TLSAuthTest()
                  log.info('Running subscriber %s tls auth test' %subscriber.name)
                  tls.runTest()
                  self.test_status = True

      def dhcp_verify(self, subscriber):
            cip, sip = self.dhcp_request(iface = subscriber.iface, update_seed = True)
            log.info('Subscriber %s got client ip %s from server %s' %(subscriber.name, cip, sip))
            subscriber.src_list = [cip]
            self.test_status = True

      def dhcp_jump_verify(self, subscriber):
          cip, sip = self.dhcp_request(seed_ip = '10.10.200.1', iface = subscriber.iface)
          log.info('Subscriber %s got client ip %s from server %s' %(subscriber.name, cip, sip))
          subscriber.src_list = [cip]
          self.test_status = True

      def dhcp_next_verify(self, subscriber):
          cip, sip = self.dhcp_request(seed_ip = '10.10.150.1', iface = subscriber.iface)
          log.info('Subscriber %s got client ip %s from server %s' %(subscriber.name, cip, sip))
          subscriber.src_list = [cip]
          self.test_status = True

      def igmp_verify(self, subscriber):
            chan = 0
            if subscriber.has_service('IGMP'):
                  for i in range(5):
                        log.info('Joining channel %d for subscriber %s' %(chan, subscriber.name))
                        subscriber.channel_join(chan, delay = 0)
                        subscriber.channel_receive(chan, cb = subscriber.recv_channel_cb, count = 1)
                        log.info('Leaving channel %d for subscriber %s' %(chan, subscriber.name))
                        subscriber.channel_leave(chan)
                        time.sleep(3)
                        log.info('Join RX stats for subscriber %s, %s' %(subscriber.name,subscriber.join_rx_stats))
                  self.test_status = True

      def igmp_jump_verify(self, subscriber):
            if subscriber.has_service('IGMP'):
                  for i in xrange(subscriber.num):
                        log.info('Subscriber %s jumping channel' %subscriber.name)
                        chan = subscriber.channel_jump(delay=0)
                        subscriber.channel_receive(chan, cb = subscriber.recv_channel_cb, count = 1)
                        log.info('Verified receive for channel %d, subscriber %s' %(chan, subscriber.name))
                        time.sleep(3)
                  log.info('Join RX stats for subscriber %s, %s' %(subscriber.name, subscriber.join_rx_stats))
                  self.test_status = True

      def igmp_next_verify(self, subscriber):
            if subscriber.has_service('IGMP'):
                  for i in xrange(subscriber.num):
                        if i:
                              chan = subscriber.channel_join_next(delay=0)
                        else:
                              chan = subscriber.channel_join(i, delay=0)
                        log.info('Joined next channel %d for subscriber %s' %(chan, subscriber.name))
                        subscriber.channel_receive(chan, cb = subscriber.recv_channel_cb, count=1)
                        log.info('Verified receive for channel %d, subscriber %s' %(chan, subscriber.name))
                        time.sleep(3)
                  log.info('Join Next RX stats for subscriber %s, %s' %(subscriber.name, subscriber.join_rx_stats))
                  self.test_status = True

      def subscriber_load(self, create = True, num = 10, num_channels = 1, channel_start = 0):
            '''Load the subscriber from the database'''
            self.subscriber_db = SubscriberDB(create = create)
            if create is True:
                  self.subscriber_db.generate(num)
            self.subscriber_info = self.subscriber_db.read(num)
            self.subscriber_list = []
            for info in self.subscriber_info:
                  self.subscriber_list.append(Subscriber(name=info['Name'], 
                                                         service=info['Service'],
                                                         num=num_channels,
                                                         channel_start = channel_start))
                  channel_start += num_channels
            
            #load the ssm list for all subscriber channels
            igmpChannel = IgmpChannel()
            ssm_groups = map(lambda sub: sub.channels, self.subscriber_list)
            ssm_list = reduce(lambda ssm1, ssm2: ssm1+ssm2, ssm_groups)
            igmpChannel.igmp_load_ssm_config(ssm_list)

      def subscriber_join_verify( self, num_subscribers = 10, num_channels = 1, 
                                  channel_start = 0, cbs = None):
          self.test_status = False
          self.num_subscribers = num_subscribers
          self.subscriber_load(create = True, num = self.num_subscribers, 
                               num_channels = num_channels, channel_start = channel_start)
          self.onos_aaa_load()
          self.thread_pool = ThreadPool(min(100, self.num_subscribers), queue_size=1, wait_timeout=1)
          if cbs is None:
                cbs = (self.tls_verify, self.dhcp_verify, self.igmp_verify)
          for subscriber in self.subscriber_list:
                subscriber.start()
                pool_object = subscriber_pool(subscriber, cbs)
                self.thread_pool.addTask(pool_object.pool_cb)
          self.thread_pool.cleanUpThreads()
          for subscriber in self.subscriber_list:
                subscriber.stop()
          return self.test_status

      def test_subscriber_join_recv(self):
          """Test subscriber join and receive""" 
          test_status = self.subscriber_join_verify(num_subscribers = 50, num_channels = 1)
          assert_equal(test_status, True)

      def test_subscriber_join_jump(self):
          """Test subscriber join and receive for channel surfing""" 
          test_status = self.subscriber_join_verify(num_subscribers = 5, 
                                                    num_channels = 50,
                                                    cbs = (self.tls_verify, self.dhcp_jump_verify, self.igmp_jump_verify))
          assert_equal(test_status, True)

      def test_subscriber_join_next(self):
          """Test subscriber join next for channels"""
          test_status = self.subscriber_join_verify(num_subscribers = 5, 
                                                    num_channels = 50,
                                                    cbs = (self.tls_verify, self.dhcp_next_verify, self.igmp_next_verify))
          assert_equal(test_status, True)
