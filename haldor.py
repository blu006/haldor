import paho.mqtt.client as mqtt
import time, signal, subprocess, http.client, urllib, hmac, hashlib, re, json
import traceback, os
#from functools import partial
import RPi.GPIO as GPIO
from daemon import Daemon
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from typing import *

class HalDaemon(Daemon):
  def run(self):
    haldor = Haldor()
    config = open("/home/brandon/haldor/haldor_config.json", "r")
    haldor.data = Haldor.data.from_json(config.read())
    config.close()

    haldor.run()

@dataclass_json
@dataclass
class Acquisition:
    name: str
    acType: str
    acObject: Union[List[str], int]

class Haldor(mqtt.Client):
  """Watches the door and monitors various switches and motion via GPIO"""

  version = '2020'
  # dataclass variable declaration
  @dataclass_json
  @dataclass
  class data:
    name: str
    description: str
    boot_check_list: Dict[str, List[str]]
    acq_io: List[Acquisition]
    gpio_path: str
    ds18b20_path: str
    mqtt_broker: str
    mqtt_port: int
  
  # TODO: Check all the GPIOs
  # 7 -> Front Door
  # 8 - Main Door
  # 25 - Office Motion
  # 11 - Shop Motion
  # 24 - Switch? "plus30Mins" in old app...
  
  def on_log(self, client, userdata, level, buff):
    if level != mqtt.MQTT_LOG_DEBUG:
      print (level)
      print(buff)
    if level == mqtt.MQTT_LOG_ERR:
      print ("error handler")
      traceback.print_exc()
      os._exit(1)

  def on_connect(self, client, userdata, flags, rc):
    print("Connected: " + str(rc))
    self.subscribe("reporter/checkup_req")

  def on_message(self, client, userdata, message):
    print("Checkup received.")
    self.checkup()

  def get_secret(self):
    if len(self.secret) <= 0:
      file = open(self.data.secret_path, 'rb')
      self.secret = file.read()
      file.close
    
    return self.secret
  
  def export_channels(self):
    GPIO.setmode(GPIO.BCM)
  
  def listen_channels(self):
    for chan in self.data.switch_channels.values():
      GPIO.add_event_detect(chan, GPIO.BOTH, callback=self.event_checkup, bouncetime=500)
      
    for chan in self.data.flip_channels.values():
      GPIO.add_event_detect(chan, GPIO.BOTH, callback=self.event_checkup, bouncetime=500)

    for chan in self.data.pir_channels.values():
      GPIO.add_event_detect(chan, GPIO.RISING, callback=self.event_checkup, bouncetime=800)
  
  def direct_channels(self):
    # pull up for switches
    # we'll need to flip it later
    for chan in self.data.switch_channels.values():
      GPIO.setup(chan, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    
    for chan in self.data.flip_channels.values():
      GPIO.setup(chan, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    for chan in self.data.pir_channels.values():
      GPIO.setup(chan, GPIO.IN, pull_up_down=GPIO.PUD_UP)
  
  def enable_gpio(self):
    self.export_channels()
    self.direct_channels()
  
  def notify_hash(self, body):
    hasher = hmac.new(self.get_secret(), body, hashlib.sha256)
    hasher.update(self.session)
    return hasher.hexdigest()
  
  def notify(self, path, params):
    # TODO: Check https certificate
    params['time'] = str(time.time())
    print (params)
    body = urllib.parse.urlencode(params).encode('utf-8')
    
    topic = self.data.name + '/' + path
    self.publish(topic, json.dumps(params))
    print("Published " + topic)
  
  def notify_bootup(self):
    boot_checks = {}

    print("Bootup:")
    
    for bc_name, bc_cmd in self.data.boot_check_list.items():
        boot_checks[bc_name] = subprocess.check_output(bc_cmd).decode('utf-8')

    self.notify('bootup', boot_checks)
  
  def bootup(self):
    print("Bootup sequence called.")
    # sort GPIOs
    self.data.switch_channels = {}
    self.data.flip_channels = {}
    self.data.pir_channels = {}
    self.data.name_ios = {}
    for gpio in self.data.acq_io:
      if gpio.acType == "SW":
        self.data.switch_channels.update({gpio.name : gpio.acObject})
        self.data.name_ios.update({gpio.acObject : gpio.name})
      if gpio.acType == "SW_INV":
        self.data.flip_channels.update({gpio.name : gpio.acObject})
        self.data.name_ios.update({gpio.acObject : gpio.name})
      if gpio.acType == "PIR":
        self.data.pir_channels.update({gpio.name : gpio.acObject})
        self.data.name_ios.update({gpio.acObject : gpio.name})

    # invert dictionary for reporting
    self.enable_gpio()
    self.notify_bootup()
    self.pings = 0
  
  def read_gpio(self, chan):
    value = GPIO.input(chan)

    return value
  
  def check_gpios(self, gpios):
    for name, chan in self.data.switch_channels.items():
      gpios[name] = self.read_gpio(chan)
    for name, chan in self.data.flip_channels.items():
      gpios[name] = int(not self.read_gpio(chan))
    for name, chan in self.data.pir_channels.items():
      gpios[name] = self.read_gpio(chan)
    
    return gpios
    
  def check_temp(self):
    value = ""
    try:
      temp = subprocess.check_output(["cat", self.data.ds18b20_path])
      match = re.search('t=(\d+)', temp.decode('utf-8'))
      value = match.group(1)
    except:
      value = "--"
    
    return value
  
  def checkup(self):
    print("Checkup.")
    checks = {}
    
    self.pings+=1
    if(self.pings % 100 == 0):
      self.pings = 0
      try:
        my_ip = subprocess.check_output(["/usr/bin/curl", "-s", "http://whatismyip.akamai.com/"]).decode('utf-8')
        checks['my_ip'] = my_ip
      except:
        print("\tmy ip read error")
      
      try:
        local_ip = subprocess.check_output(["/home/brandon/haldor/local_ip.sh"]).decode('utf-8')
        checks['local_ip'] = local_ip
      except:
        print("\tlocal ip read error")
    
    self.check_gpios(checks)
    checks['Temperature'] = self.check_temp()
    self.notify('checkup', checks)
  
  def event_checkup(self, channel):
    checks = {}
    name = self.data.name_ios[channel]
    print("Event caught for {0}".format(name))
    if name in self.data.flip_channels:
      checks[name] = int(not self.read_gpio(channel))
    else:
      checks[name] = self.read_gpio(channel)
    self.notify('checkup', checks)
  
  def run(self):
    self.connect(self.data.mqtt_broker, self.data.mqtt_port, 60)
    self.bootup()
    self.listen_channels()
    
    while True:
      self.loop_forever()
      # Threaded event detection will execute whenever it detects a change.
      # This main loop will sleep and send data every 5 minutes regardless of how often stuff changes
      
