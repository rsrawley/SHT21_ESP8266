import uasyncio # Asynchronous behaviour
import os # For filesystem access
from machine import Pin, I2C, RTC
import time
import socket
import network
from uselect import select
import ntptime


# ********** NETWORK SETUP **********
# Wifi connection function
def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print('connecting to network...')
        wlan.connect('The Rainbow Collective', 'RMjojumel5')
        while not wlan.isconnected():
            pass
    print('IP address is:', wlan.ifconfig()[0])
    
def disable_ap():
    # Disable Access point
    ap_if = network.WLAN(network.AP_IF)
    ap_if.active(False)
    
# Network
disable_ap()
connect_wifi()


# ********** CLOCK SYNCHRONISATION **********
# The RTC on the ESP8266 is horrible and ticks faster anywhere between 5 to 12 seconds every 5 minutes!
# Sync to server every minute to try to keep somewhat synchronised

# Real time clock
rtc = RTC()

async def syncTime():
    while True:
        currentTime = time.time()
        
        try:
            ntptime.settime() # Set the rtc datetime from the remote server
        except OSError as error:
            print(error)
            pass

        timeDrift = time.time() - currentTime
        print("Synced time is: ",rtc.datetime(), " with time drift:",timeDrift)
        await uasyncio.sleep(60) # Wait 1 minute

def now():
    time = rtc.datetime()
    time = time[0]
    
    
# ********** WRITING TO DATA FILE **********
# Write to file
def writeFile(values):
    fileSizeLimit = 6000 # About 25 h (each line of CSV is 20 bytes)
    if "values1.dat" in os.listdir() and os.stat("values1.dat")[6] > fileSizeLimit: # File 1 too big
        file = 2
    else:
        file = 1

    #print ("writing in file","values" + str(file) + ".dat")
    f = open("values" + str(file) + ".dat","a")
    f.write(",".join(map(str,values)) + "\n") # Convert to string and concatenate with commas
    f.close()

    # Check again if file too big with recent write
    # 21 bytes per line, readings every 5 min, for 24 hours that's at least 6048 bytes
    if os.stat("values" + str(file) + ".dat")[6] > fileSizeLimit:
        #print("erasing file",str(3 - file))
        f = open("values" + str(3 - file) + ".dat","w") # Overwrite file
        f.close()


# ********** SENSOR SETUP **********
def readSensor():
    # Constants for converting readings to true values
    readings = [
        {"command": "TEMP_MEAS", "a": -46.85, "b": 175.72, "value": None},
        {"command": "HUM_MEAS", "a": -6, "b": 125, "value": None}
    ]

    for measurement in readings:
        buf = sht21.i2cRead(measurement["command"],3)
        buf = (buf[0] << 8 | buf[1]) & 0xFFFC
        measurement["value"] = round(measurement["a"] + measurement["b"] * buf / 65536, 1); # In datasheet, 65536 is listed as 2^16
    
    return [readings[0]["value"],readings[1]["value"]]


# Define a class for the SHT21 sensor
class Sensor:
    i2c = I2C(scl=Pin(5), sda=Pin(4), freq=400000) # Define i2c bus

    def __init__(self, DEVICE_ADDR, CONSTANTS):
        self.deviceAddr = DEVICE_ADDR
        self.constants = CONSTANTS

    # Function to write to i2c
    def i2cWrite(self,command):
        #print("Writing to i2c...",hex(command))
        if type(command) is str:
            command = self.constants[command]
            
        val = self.i2c.writeto(self.deviceAddr, bytearray([command]))
        #print(val)
        
    # Function to read from i2c
    def i2cRead(self,command,numBytes):
        #print("Reading i2c...",hex(command)," for ",numBytes," bytes")
        if type(command) is str:
            command = self.constants[command]

        self.i2cWrite(command)
        return self.i2c.readfrom(self.deviceAddr, numBytes)


async def checkSensor():
    global sampleTime # For webpage
    
    sampleTime = 5 # In minutes
    while True:
        currentTime = time.time() + 946684800 # Micropython time (starts January 1, 2000) is off by this much from Unix Epoch time
        values = readSensor() # Read sensor
        values.insert(0,currentTime) # Add time at beginning
        writeFile(values)
        await uasyncio.sleep(sampleTime*60)


# i2c
sht21 = Sensor(0x40, {
    "TEMP_MEAS": 0xE3, # Hold master (SCL line will be held low and disrupt I2C until measurement is done)
    "HUM_MEAS": 0xE5, # Hold master
    "SOFT_RESET": 0xFE
})

sht21.i2cWrite("SOFT_RESET") # Reset sensor
time.sleep(0.05) # Wait for sensor to come up


# ********** WEB SERVER **********
addr = socket.getaddrinfo('0.0.0.0', 80)[0][-1]
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(addr)
s.listen(5)
print('listening on', addr)

async def webServer():
    while True:
        r, w, err = select((s,), (), (),1)
        #print("timeout")
        if r:
            for readable in r:
                cl, addr = s.accept()
                print ("Connected client from ",addr)
                try:
                    client_handler(cl)
                except OSError as e:
                    pass

        await uasyncio.sleep(0) # 0 to give up execution for other routine to run


# Handles HTML requests
def client_handler(client):
    path = getHeaders(client)["GET"].replace("/","")
      
    # Serve file
    if path == "":
        path = "index.html"
    
    if path.endswith("html"):
        content = "text/html"
    elif path.endswith("dat"):
        content = "text/csv"
    else:
        content = "text/plain"
    
    try:
        length = os.stat(path)[6] # If file not found, will fail here and go straight to except:
        #print(length)
        client.send('HTTP/1.1 200 OK\n')
        client.send('Content-Type: ' + content + '\n')
        client.send('Content-Length: ' + str(length) + '\n')
        
        if content == "text/csv":
            client.send('X-sampleTime: ' + str(sampleTime) + '\n') # Custom header on data files
                
        client.send('\n') # Need final newline to end header block

        file = open(path,"rb")
        while True:
            chunk = file.read(1024) # Read 1024 at a time to keep memory usage low
            if not chunk:
                break
            client.write(chunk)

    except:
        client.send("HTTP/1.0 404 Not Found")
    
    #time.sleep(0.3) # Don't know why, but without a delay, client gets "ERR_CONNECTION_reset"
    client.close()


# Retrieve headers from HTTP request
def getHeaders(client):
    # Receive all headers
    req = client.recv(1024).decode("utf-8").split("\r\n")
    
    headers = {}
    for header in req: # Break up req into key/value dictionary
        reqHeader = header.split(" ")
        if len(reqHeader) > 1:
            headers[reqHeader[0]] = reqHeader[1]

    return headers


# ********** MAIN PROGRAM **********
async def main():
    await uasyncio.gather(
        syncTime(),
        checkSensor(),
        webServer()
    )
    
# Main loop
# Entry point for asyncio
uasyncio.run(main())


"""
# ********** RELAY **********
# Define a class for the relay
class Relay:
    def __init__(self, GPIO_PWR, GPIO_CNTRL):
        Pin(GPIO_PWR, Pin.OUT).value(1) # Relay power, just needs to be on
        self.relay = Pin(14, Pin.OUT) # Control

    def on(self):
        self.relay.value(0)

    def off(self):
        self.relay.value(1)
    
    def toggle(self):
        self.relay.value(not self.relay.value())
        return int(not self.relay.value())

# Relay setup
#relay = Relay(13, 14) # D7 and D5
#relay.off()
"""